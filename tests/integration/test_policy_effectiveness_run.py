import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from test_native_calculator_results import LEGACY, OBSERVATION, ROOT
from test_quality_result_api import _client, _fk_enforcing_engine

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.calculators.policy_effectiveness.calculator import (
    HTML_COORDINATES,
    PolicyEffectivenessCalculator,
    body_text,
)
from qa_eval.calculators.protocol import CalculatorContext
from qa_eval.contracts.datasets import DatasetManifestV2, SampleRecordV2
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

PARAMETERS = {"reference_date": "2026-11-19", "observation_date": "2026-11-19"}
OBSERVE_POLICY = {
    **OBSERVATION,
    "key": "policy-effectiveness",
    "calculator_key": "policy_effectiveness",
    "parameters": PARAMETERS,
}
EXPECTED = {
    "input_count": 34,
    "applicable_count": 14,
    "not_applicable_count": 4,
    "applicability_unknown_count": 16,
    "expired_evidenced_count": 6,
    "not_expired_by_known_term_count": 2,
    "undetermined_count": 6,
    "decidable_count": 8,
    "decidable_coverage": 8 / 14,
    "expired_evidence_rate": 6 / 14,
}


def _select_review_records(source_path, destination, ids):
    # Read the published JSONL without running any Calculator on the other rows.
    # The byte search avoids deserializing unrelated full-text records.
    candidates = b"|".join(re.escape(value.encode()) for value in sorted(ids))
    pattern = re.compile(rb'"sample_id"\s*:\s*"(' + candidates + rb')"')
    remaining = set(ids)
    with source_path.open("rb") as source, destination.open("wb") as output:
        for line in source:
            if pattern.search(line):
                key = json.loads(line)["sample_id"]
                if key in remaining:
                    output.write(line)
                    remaining.remove(key)
                    if not remaining:
                        break
    assert not remaining, f"{len(remaining)} reviewed records were not found"


def seed_review_subset(session, store, samples_path, manifest_path):
    manifest = DatasetManifestV2.model_validate_json(manifest_path.read_bytes())
    version_id = manifest.dataset_version_id
    samples = samples_path.read_bytes()
    assert len(samples.splitlines()) == 34
    sample_ref = store.put_file("phase1/sample_records.jsonl", samples_path)
    normalized = (
        manifest.artifacts[0]
        .model_copy(
            update={
                "artifact_id": uuid4(),
                "kind": "NORMALIZED_DATA",
                "uri": sample_ref.uri,
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/sample-record/2.0.schema.json",
                "record_count": 34,
                "size_bytes": len(samples),
            }
        )
        .model_dump(mode="json")
    )
    normalized["checksum"]["value"] = sample_ref.sha256
    subset = manifest.model_dump(mode="json")
    subset["artifacts"] = [normalized]
    subset["source_snapshot"]["record_count"] = 34
    subset["source_snapshot"]["details"]["purpose"] = "Phase 1 reviewed subset"
    manifest_key = "phase1/dataset_manifest.json"
    store.prepare_write(manifest_key).write_text(json.dumps(subset))
    manifest_ref = store.commit(manifest_key)
    session.add(
        DatasetModel(id=manifest.dataset_id, name=manifest.name, modality=["text"])
    )
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=manifest.dataset_id,
            version="phase1-34",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{sample_ref.sha256}",
            artifact_refs={
                "manifest": {"uri": manifest_ref.uri, "sha256": manifest_ref.sha256},
                "normalized": {"uri": sample_ref.uri, "sha256": sample_ref.sha256},
                "field_mapping": subset["field_mapping"],
            },
        )
    )
    session.flush()
    MetricRegistryService(session).sync()
    return version_id


def test_reviewed_published_sample_through_worker_and_evidence_api(tmp_path):
    sample_file = os.environ.get("QA_EVAL_POLICY_REVIEW_RECORDS")
    manifest_file = os.environ.get("QA_EVAL_POLICY_REVIEW_MANIFEST")
    if not sample_file or not manifest_file:
        pytest.skip(
            "provide the 34 full published SampleRecords and their original Manifest"
        )
    manifest_path = Path(manifest_file)
    review_path = ROOT / "docs/development/0205-effectiveness-audit/phase1_review.jsonl"
    review = {
        row["record_id"]: row
        for row in map(json.loads, review_path.read_text().splitlines())
    }
    assert len(review) == 34
    samples_path = tmp_path / "review_samples.jsonl"
    _select_review_records(Path(sample_file), samples_path, review)
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        version = seed_review_subset(session, store, samples_path, manifest_path)
        service = EvaluationRunService(session, store)
        common = {
            "name": "Phase 2B",
            "version_label": "1",
            "dataset_version_id": version,
        }
        runs = []
        for items, observations in (
            ([LEGACY], []),
            ([LEGACY], [OBSERVE_POLICY]),
            ([], [OBSERVE_POLICY]),
        ):
            request = QualityContractMultiPublishRequest.model_validate(
                {
                    **common,
                    "items": items,
                    "observation_items": observations,
                }
            )
            contract = QualityContractService(session).publish_multi(request)
            run = service.create(contract.contract.contract_version_id)
            session.commit()
            complete = service.execute(run.run_id)
            assert complete.status == "SUCCEEDED", complete.error
            runs.append(run.run_id)
        baseline, mixed, repeat = runs
        assert (
            len(service.list_results(baseline)) == len(service.list_results(mixed)) == 1
        )
        assert service.list_results(repeat) == []
        [first] = service.list_calculator_results(mixed)
        [second] = service.list_calculator_results(repeat)
        first_ref, second_ref = (
            first.decision_artifact_refs[0],
            second.decision_artifact_refs[0],
        )
        assert first.statistics == second.statistics == EXPECTED
        assert first_ref.artifact.checksum == second_ref.artifact.checksum
        assert first_ref.artifact.record_count == 34
        assert first.status_detail["document_type_source"] == "extra.type"
    with _client(engine, store) as client:
        prefix = f"/api/v1/evaluation-runs/{mixed}"
        assert (
            client.get(
                f"{prefix}/calculator-results/{first.calculator_result_id}"
            ).json()["statistics"]
            == EXPECTED
        )
        evidence = f"/api/v1/evidence/{first_ref.evidence_id}"
        assert client.post(f"{evidence}/index/rebuild").status_code == 200
        rows = client.get(f"{evidence}/rows", params={"limit": 100}).json()["rows"]
        assert len(rows) == len({row["sample_id"] for row in rows}) == 34
        key = first_ref.summary.attributes["artifact_key"]
        assert all(
            ref["artifact_key"] == key and ref["row_id"] == row["row_id"]
            for row in rows
            for ref in row["evidence_refs"]
        )
        records = [
            SampleRecordV2.model_validate_json(line)
            for line in samples_path.read_bytes().splitlines()
        ]
        samples = {record.sample_id: record for record in records}
        for row in rows:
            expected = review[row["sample_id"]]
            assert row["attributes"]["applicability"] == expected["applicability"], row[
                "sample_id"
            ]
            assert row["attributes"]["effectiveness"] == expected["decision"], row[
                "sample_id"
            ]
            assert row["reason"] == expected["reason_code"], row["sample_id"]
            by_id = {c.content_id: c for c in samples[row["sample_id"]].contents}
            for loc in row["locations"]:
                text, coordinate = body_text(by_id[loc["content_id"]])
                assert loc["coordinate_space"] == coordinate
                assert 0 <= loc["start"] < loc["end"] <= len(text)
                assert (
                    loc["attributes"]["text_fingerprint"]
                    == f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
                )
        assert any(
            loc["coordinate_space"] == HTML_COORDINATES
            for row in rows
            for loc in row["locations"]
        )
        base = f"/api/v1/evaluation-runs/{baseline}"
        baseline_report = client.get(f"{base}/quality-report").json()
        mixed_report = client.get(f"{prefix}/quality-report").json()
        for key in ("gate_status", "coverage", "scoring", "issue_summary"):
            assert baseline_report[key] == mixed_report[key]
        scored = [
            client.post(f"{path}/quality-report/operational-scoring/rebuild")
            for path in (base, prefix)
        ]
        assert all(response.status_code == 200 for response in scored)
        for key in ("overall_estimated_score", "estimated_dimension_coverage", "radar"):
            assert scored[0].json()[key] == scored[1].json()[key]
        assert client.get(f"{prefix}/quality-issues").json()["total"] == 0

    digest = tmp_path / "duplicate.jsonl"
    data = samples_path.read_bytes()
    digest.write_bytes(data + data.splitlines(keepends=True)[0])
    context = CalculatorContext(
        uuid4(),
        uuid4(),
        version,
        "0205",
        "1.0.0",
        digest,
        tmp_path / "decision-output",
        "2.0",
        {"DATASET_MANIFEST": manifest_path},
    )
    outcome = PolicyEffectivenessCalculator().execute(context, PARAMETERS)
    assert outcome.status_detail["duplicate_input_rows"] == 1
    assert outcome.statistics == EXPECTED
    assert outcome.decisions.record_count == 34
    today = PolicyEffectivenessCalculator().execute(
        replace(
            context,
            input_path=samples_path,
            output_dir=tmp_path / "today",
        ),
        {},
    )
    assert today.status_detail["observation_date"] == "2026-11-05"

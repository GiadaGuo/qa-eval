"""Published v2/v3 parity for the four M4-B bindings."""

import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.mapping_revision_service import MappingRevisionService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.contracts.logical_inputs import FieldBinding, MappingRevision
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

BODY = "企业补贴金额为1万元。企业补贴金额为2万元。企业补贴金额为1万元。"
ROLE = "article.body.plain"
FIELD = {"selector": "content_role", "role": ROLE}
RELATION_FIELDS = {
    "declared": {
        "selector": "record_pointer",
        "path": "/metadata/left",
        "value_type": "string",
    },
    "observed": {
        "selector": "record_pointer",
        "path": "/metadata/right",
        "value_type": "string",
    },
}


def seed(session, store, dataset_id, v3):
    version = uuid4()
    digest = "sha256:" + hashlib.sha256(b"fixture schema").hexdigest()
    source = []
    for index, (body, right) in enumerate(
        ((BODY, "ABC"), (BODY, "other"), (None, None))
    ):
        if v3:
            source.append(
                {"payload": body, "claim": "abc", "facts": [{"result": right}]}
            )
            continue
        content = {
            "content_id": "plain",
            "modality": "text",
            "role": ROLE,
            "presence": "NULL" if body is None else "PRESENT",
        }
        if body is not None:
            content["payload"] = {
                "type": "inline_text",
                "format": "plain",
                "text": body,
            }
        source.append(
            {
                "schema_version": "2.0",
                "dataset_version_id": str(version),
                "sample_id": str(index),
                "contents": [content],
                "annotations": [],
                "metadata": {"left": "abc", "right": right},
                "source": {"source_artifact_id": str(uuid4())},
                "lineage": {"input_fingerprint": digest, "transforms": []},
            }
        )
    uri = "raw.jsonl" if v3 else "normalized.jsonl"
    store.prepare_write(uri).write_text(
        "".join(json.dumps(row) + "\n" for row in source)
    )
    artifact = store.commit(uri)
    if v3:
        refs = {
            "raw_record_manifest": {
                "dataset_version_id": str(version),
                "source_uri": uri,
                "source_sha256": "sha256:" + artifact.sha256,
                "schema_fingerprint": digest,
            }
        }
    else:
        refs = {
            "manifest": {"uri": "unused.json"},
            "normalized": {"uri": uri, "sha256": artifact.sha256},
            "field_mapping": {
                "mappings": [
                    {
                        "action": "MAP",
                        "target": {
                            "kind": "CONTENT",
                            "role": ROLE,
                            "modality": "text",
                        },
                    }
                ]
            },
        }
    session.add(
        DatasetVersionModel(
            id=version,
            dataset_id=dataset_id,
            version=uri,
            status="PUBLISHED",
            artifact_refs=refs,
        )
    )
    session.commit()
    return version


@pytest.mark.parametrize("alternate", [False, True])
@pytest.mark.parametrize(
    "key,metric,measurement,old_version",
    [
        ("content_consistency", "0206", "consistent_record_ratio", "2.0.0"),
        (
            "field_relation_consistency",
            "0207",
            "confirmed_consistent_record_ratio",
            "2.0.0",
        ),
        ("cross_record_duplicate", "0208", "duplicate_redundant_record_ratio", "1.0.0"),
        ("intra_record_repetition", "0208", "intra_record_repetition_score", "1.0.0"),
    ],
)
def test_m4b_pair(
    tmp_path, monkeypatch, key, metric, measurement, old_version, alternate
):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    if metric == "0206" and alternate:
        monkeypatch.setenv("QA_EVAL_0206_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    materialize = store.materialize

    def no_raw_copy(uri, destination):
        assert uri != "raw.jsonl"
        return materialize(uri, destination)

    monkeypatch.setattr(store, "materialize", no_raw_copy)
    dataset = uuid4()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    parameters = {"fields": [FIELD]}
    selectors = {"0": {"field_tokens": ["payload"]}}
    if metric == "0206":
        parameters = (
            {"mode": "rule_embedding", "embedding_backend": "concept"}
            if alternate
            else {}
        )
        selectors = {"plain": {"field_tokens": ["payload"]}}
    elif metric == "0207":
        parameters = {
            "fields": RELATION_FIELDS,
            "relations": [
                {
                    "id": "agreement",
                    "left": "declared",
                    "right": "observed",
                    "weight": 1,
                    "method": "exact_match",
                    "required": True,
                    "parameters": {"normalizer": "unicode_casefold_v1"}
                    if alternate
                    else {},
                }
            ],
        }
        selectors = {
            "declared": {"field_tokens": ["claim"]},
            "observed": {"field_tokens": ["facts", "0", "result"]},
        }
    elif key == "cross_record_duplicate":
        parameters["strategies"] = (
            ["field_exact", "field_near"] if alternate else ["record_exact"]
        )
        parameters["near_threshold"] = 0.8
    else:
        parameters.update(min_length=4, repetition_threshold=0.2)
        if alternate:
            parameters["local_ngram_size"] = 8

    results = []
    evidence_sets = []
    native = metric in {"0206", "0207"}
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset, name="M4 B fixture", modality=["text"]))
        session.commit()
        MetricRegistryService(session).sync()
        runner = EvaluationRunService(session, store)
        for v3 in (False, True):
            version = seed(session, store, dataset, v3)
            implementation = old_version[0] + ".1.0" if v3 else old_version
            rule = {"operator": "GTE", "threshold": 0.9}
            item = {
                "key": key,
                "metric_key": metric,
                "metric_version": "2.0.0",
                "calculator_key": key,
                "calculator_version": implementation,
                "parameters": parameters,
            }
            if v3:
                item["input_selectors"] = selectors
                if alternate and key in {
                    "cross_record_duplicate",
                    "intra_record_repetition",
                }:
                    raw = session.get(DatasetVersionModel, version).artifact_refs[
                        "raw_record_manifest"
                    ]
                    revision = MappingRevision(
                        dataset_version_id=version,
                        schema_fingerprint=raw["schema_fingerprint"],
                        bindings=(
                            FieldBinding(
                                semantic="fixture.body",
                                representation="text/plain",
                                field_tokens=("payload",),
                            ),
                        ),
                    )
                    item["mapping_digest"] = MappingRevisionService(session).confirm(
                        revision
                    )
                    item["input_selectors"] = {"0": {"semantic": "fixture.body"}}
                if alternate and metric == "0207":
                    item["parameters"] = parameters | {
                        "fields": {
                            name: {"value_type": spec["value_type"]}
                            for name, spec in RELATION_FIELDS.items()
                        }
                    }
                elif alternate and metric == "0208":
                    item["parameters"] = parameters | {"fields": [{}]}
            if metric == "0206":
                item.update(
                    schema_version="1.1",
                    strategy="EVALUATE",
                    projections=[],
                    statistic_ref={
                        "contract_item_key": key,
                        "output_slot_id": key,
                        "calculator_version": implementation,
                        "statistics_key": measurement,
                    },
                    rule=rule,
                    severity="WARNING",
                )
            else:
                item.update(
                    measurement=measurement,
                    rule={**rule, "severity": "WARNING", "on_missing": "UNKNOWN"},
                )
                if metric == "0208":
                    item["operator_slot"] = key
                    item.pop("calculator_key")
                    item.pop("calculator_version")
            contract = (
                QualityContractService(session)
                .publish_multi(
                    QualityContractMultiPublishRequest.model_validate(
                        {
                            "name": f"{key}-{v3}",
                            "version_label": "1",
                            "dataset_version_id": version,
                            "items": [] if metric == "0206" else [item],
                            "observation_items": [item] if metric == "0206" else [],
                        }
                    )
                )
                .contract
            )
            run = runner.create(contract.contract_version_id)
            session.commit()
            completed = runner.execute(run.run_id)
            assert completed.status == "SUCCEEDED", completed.error
            if native:
                (result,) = runner.list_calculator_results(run.run_id)
                assert result.calculator_version == implementation
                (evidence,) = result.decision_artifact_refs
                results.append((result.status, result.statistics))
            else:
                (result,) = runner.list_results(run.run_id)
                (evidence,) = result.evidence
                results.append((result.status, result.observations, result.population))
            rows = list(EvidenceReader(store).iter_jsonl(evidence))
            assert rows
            if v3:
                query = EvidenceQueryService(session, store)
                for row in rows:
                    UUID(row["sample_id"])
                    assert query.get_sample_by_id(version, row["sample_id"]) is not None
                    if key == "cross_record_duplicate":
                        representative = query.get_sample_by_id(
                            version, row["representative_sample_id"]
                        )
                        assert representative.meta.line_number == 1
                    elif key == "field_relation_consistency":
                        source = row["attributes"]["fields"]["declared"]["source"]
                        assert source["source_origin"]["field_tokens"] == ["claim"]
                    elif (
                        key == "content_consistency"
                        and row["status"] != "NOT_EVALUATED"
                    ):
                        assert row["attributes"]["source_origins"]["plain"][
                            "field_tokens"
                        ] == ["payload"]
            if native:
                evidence_sets.append([(row["status"], row["reason"]) for row in rows])
            elif key == "cross_record_duplicate":
                evidence_sets.append(
                    [(row["group_id"], row.get("member_count")) for row in rows]
                )
            else:
                evidence_sets.append(
                    [[entry["ranges"] for entry in row["locations"]] for row in rows]
                )
    assert results[0] == results[1]
    assert evidence_sets[0] == evidence_sets[1]

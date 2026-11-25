from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from qa_eval.application.evaluation.unified_report import coverage_gate
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.finalization_backend import NativeFinalizationBackend
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.scoring_consumer import RecordCounts, ScoringConsumer
from qa_eval.application.services.unified_finalization import (
    FinalizationBlocked,
    UnifiedFinalization,
)
from qa_eval.contracts.quality import FrozenCoveragePolicy
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)
from scripts.validate_policy_document_format import run_isolated


@pytest.mark.parametrize(
    ("known", "total", "threshold", "outcome"),
    [
        (8, 10, 0.8, "PASS"),
        (7, 10, 0.8, "FAIL"),
        (7, 10, 0.7, "PASS"),
        (0, 10, 0, "PASS"),
        (0, 0, 0, "INDETERMINATE"),
        (8, 10, None, "INDETERMINATE"),
    ],
)
def test_frozen_coverage(known, total, threshold, outcome):
    policy = (
        FrozenCoveragePolicy(coverage_min=threshold, source="CONTRACT_SLOT")
        if threshold is not None
        else None
    )
    counts = RecordCounts(
        population=total,
        passed=known,
        failed=0,
        unknown=total - known,
        not_applicable=0,
    )
    assert (
        coverage_gate(SimpleNamespace(counts=counts, status="PARTIAL"), policy).verdict
        == outcome
    )


def test_local_native_pilot_stops_at_missing_production_scoring(tmp_path):
    folder = next(
        Path("data/artifacts/datasets").glob("1aa1f0ce-*/*/sample_records.jsonl"), None
    )
    if folder is None:
        pytest.skip("local published policy records unavailable")
    subset = tmp_path / "subset.jsonl"
    with folder.open("rb") as source:
        rows = list(islice(source, 12))
    if len(rows) < 12:
        pytest.skip("published snapshot too small")
    subset.write_bytes(b"".join(rows))
    result = run_isolated(
        tmp_path, subset, folder.with_name("dataset_manifest.json"), len(rows)
    )
    assert result["result"]["statistics"]["input_count"] == 12
    assert result["report"]["native_evaluations"]
    run_id = UUID(result["run_id"])
    engine = create_engine(f"sqlite:///{tmp_path / 'run.sqlite3'}")
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as reader, Session(engine) as writer:
        consumer = ScoringConsumer(reader, EvidenceQueryService(reader, store))
        inputs = consumer.read(run_id)
        assert inputs.run.execution["selected_attempts"]
        policy = inputs.coverage_policy(inputs.components[0].key)
        assert policy.coverage_min == 0.8 and policy.source == "SYSTEM_DEFAULT"
        assert "P1_FROZEN_SCORING_CONFIGURATION_UNAVAILABLE" in inputs.blockers
        assert len(consumer.rules(inputs, inputs.components[0].key)) == 1
        item = inputs.contract.observation_items[0]
        published = QualityContractService(reader).publish_multi(
            QualityContractMultiPublishRequest.model_validate(
                {
                    "name": "explicit zero",
                    "version_label": "1",
                    "dataset_version_id": inputs.run.dataset_version_id,
                    "observation_items": [
                        {
                            "schema_version": "1.1",
                            "key": item.key,
                            "strategy": "EVALUATE",
                            "metric_key": item.metric.key,
                            "metric_version": item.metric.version,
                            "calculator_key": inputs.components[
                                0
                            ].result.calculator_key,
                            "calculator_version": inputs.components[
                                0
                            ].result.calculator_version,
                            "projections": [],
                            "statistic_ref": item.statistic_ref.model_dump(mode="json"),
                            "rule": item.rule.model_dump(mode="json"),
                            "severity": item.severity,
                            "coverage_min": 0,
                        }
                    ],
                }
            )
        )
        assert published.contract.observation_items[
            0
        ].coverage_policy == FrozenCoveragePolicy(
            coverage_min=0,
            source="CONTRACT_SLOT",
        )
        reader.rollback()
        NativeFinalizationBackend(sessionmaker(engine), store).verify_artifacts(inputs)
        with pytest.raises(FinalizationBlocked, match="UNAVAILABLE"):
            UnifiedFinalization.for_store(writer, store).finalize_run(consumer, run_id)
    engine.dispose()

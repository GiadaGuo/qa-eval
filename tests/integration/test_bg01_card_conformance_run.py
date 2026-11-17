from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    ContractRuleRequest,
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.dataset_card_conformance import (
    DatasetCardConformanceCalculator,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_card import DatasetCardModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

ROOT = Path(__file__).resolve().parents[2]


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _seed(session: Session, store: LocalFileSystemArtifactStore):
    dataset_id, version_id, profile_id = uuid4(), uuid4(), uuid4()
    normalized_path = store.prepare_write("datasets/bg01/normalized.jsonl")
    normalized_path.write_text("", encoding="utf-8")
    normalized = store.commit("datasets/bg01/normalized.jsonl")
    manifest_path = store.prepare_write("datasets/bg01/manifest.json")
    manifest_path.write_text('{"schema_version":"2.0"}', encoding="utf-8")
    manifest = store.commit("datasets/bg01/manifest.json")
    session.add(DatasetModel(id=dataset_id, name="bg01", modality=["text"]))
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{normalized.sha256}",
            artifact_refs={
                "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                "manifest": {"uri": manifest.uri, "sha256": manifest.sha256},
                "field_mapping": {
                    "schema_version": "2.0",
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {
                                "kind": "CONTENT",
                                "role": "article.body.plain",
                                "modality": "text",
                            },
                        }
                    ],
                },
            },
            published_at=datetime.now(UTC),
        )
    )
    session.flush()
    profile_document = {
        "schema_version": "1.0",
        "profile_id": str(profile_id),
        "dataset_version_id": str(version_id),
        "structure": {
            "modalities": ["text"],
            "content_roles": ["article.body.plain"],
        },
    }
    session.add(
        DatasetProfileModel(
            id=profile_id,
            dataset_version_id=version_id,
            document=profile_document,
            checksum_sha256=_sha(profile_document),
            profiler_key="base_dataset_profiler",
            profiler_version="1.0.0",
            config_fingerprint="sha256:" + "a" * 64,
        )
    )
    card_document = {
        "schema_version": "1.0",
        "basic_information": {"name": "BG-01 fixture"},
        "content_characteristics": {"modality": "text"},
        "construction_process": {"source": "integration fixture"},
        "application_description": {},
    }
    session.add(
        DatasetCardModel(
            id=uuid4(),
            dataset_version_id=version_id,
            revision=1,
            lifecycle_status="PUBLISHED",
            document=card_document,
            content_sha256=_sha(card_document),
            published_at=datetime.now(UTC),
        )
    )
    session.flush()
    return version_id


def _publish(session: Session, version_id, *, observe_only: bool = False):
    return QualityContractService(session).publish(
        QualityContractPublishRequest(
            name="BG-01 card conformance",
            version_label="v1-observe" if observe_only else "v1-threshold",
            dataset_version_id=version_id,
            metric_key="BG01_CARD_CONFORMANCE",
            metric_version="1.0.0",
            measurement="four_element_completeness_ratio",
            calculator_key="dataset_card_conformance",
            calculator_version="1.0.0",
            parameters={"rule_set_ref": "bg01-card-rules@1.0.0"},
            rule=ContractRuleRequest(
                strategy="OBSERVE_ONLY" if observe_only else "THRESHOLD",
                operator=None if observe_only else "GTE",
                threshold=None if observe_only else 1,
                severity="ERROR",
                on_missing="SKIPPED" if observe_only else "UNKNOWN",
            ),
        )
    )


def test_bg01_registry_to_report_full_chain(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        version_id = _seed(session, store)
        MetricRegistryService(session).sync()
        descriptor = next(
            item
            for item in MetricRegistryService(session).list_calculators()
            if item.calculator_key == "dataset_card_conformance"
        )
        TrustedCalculatorLoader().load(descriptor)
        contract = _publish(session, version_id)
        run_service = EvaluationRunService(session, store)
        run = run_service.create(contract.contract.contract_version_id)
        session.commit()
        completed = run_service.execute(run.run_id)
        result = run_service.list_results(run.run_id)[0]
        quality = QualityResultService(session, store)
        rules = quality.list_rules(run.run_id)
        total, issues = quality.list_issues(run.run_id)
        report = quality.get_report(run.run_id)

        assert completed.status == "SUCCEEDED"
        assert result.status == "PARTIAL"
        values = {item.key: item.value for item in result.observations}
        assert values == {"four_element_completeness_ratio": 0.75}
        assert result.semantic_coverage is not None
        assert "basic.name" in result.semantic_coverage.excluded
        assert result.status_detail["not_evaluated_reasons"] == ["RULE_NOT_APPROVED"]
        assert rules[0].status == "UNKNOWN"
        assert rules[0].reason_code == "PARTIAL_RESULT"
        assert report is not None
        assert report.metrics[0].metric.key == "BG01_CARD_CONFORMANCE"
        assert report.metrics[0].score is None
        assert total == len(issues)
        assert any(issue.code == "CARD_MISSING" for issue in issues)
        assert not any(issue.code == "THRESHOLD_VIOLATED" for issue in issues)

        evidence = result.evidence[0]
        assert evidence.kind == "BG01_CARD_CHECKS"
        rows = [
            json.loads(line)
            for line in store.read(evidence.artifact.uri).decode().splitlines()
        ]
        schema = json.loads(
            (
                ROOT / "schemas/qa-eval/evidence/bg01-card-checks/1.0.0.schema.json"
            ).read_text()
        )
        validator = Draft202012Validator(schema)
        for row in rows:
            validator.validate(row)
        assert any(
            row["comparison_id"] == "element.application_description"
            and row["outcome"] == "MISSING"
            for row in rows
        )
        assert any(
            row["comparison_id"] == "basic.name"
            and row["outcome"] == "NOT_EVALUATED"
            and row["reason_code"] == "RULE_NOT_APPROVED"
            for row in rows
        )


def test_observe_only_rule_is_skipped_and_creates_no_rule_failure_issue(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        version_id = _seed(session, store)
        MetricRegistryService(session).sync()
        contract = _publish(session, version_id, observe_only=True)
        runs = EvaluationRunService(session, store)
        run = runs.create(contract.contract.contract_version_id)
        session.commit()
        runs.execute(run.run_id)
        quality = QualityResultService(session, store)
        rules = quality.list_rules(run.run_id)
        _, issues = quality.list_issues(run.run_id)

        assert rules[0].status == "SKIPPED"
        assert rules[0].reason_code == "PARTIAL_RESULT"
        assert not any(issue.code == "THRESHOLD_VIOLATED" for issue in issues)


def test_descriptor_digest_matches_implementation() -> None:
    descriptor = json.loads(
        (
            ROOT / "configs/registry/calculators/dataset-card-conformance-v1.0.0.json"
        ).read_text()
    )
    assert descriptor["code_digest"] == TrustedCalculatorLoader.code_digest(
        DatasetCardConformanceCalculator()
    )

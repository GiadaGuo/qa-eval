# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# EvaluationRun 删除：事务一致性、关联数据清理与状态保护。
# ====================================================

import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.domain.exceptions import EvaluationRunNotDeletableError
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    MetricResultModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    IssueIndexModel,
    QualityIssueModel,
    QualityReportModel,
    RuleEvaluationModel,
    ScoredQualityReportModel,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _fk_enforcing_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _sample(version_id, sample_id: str, text: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "body",
                    "modality": "text",
                    "role": "article.body.plain",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": text, "format": "plain"},
                    "attributes": {},
                }
            ],
            "annotations": [],
            "metadata": {},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "locator": None,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def _seed_contract(session: Session, store: LocalFileSystemArtifactStore):
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    content = (
        "\n".join(
            [
                _sample(version_id, "a", "重复正文"),
                _sample(version_id, "b", "重复正文"),
                _sample(version_id, "c", "独立正文"),
            ]
        )
        + "\n"
    )
    staging = store.prepare_write(relative)
    staging.write_text(content)
    normalized = store.commit(relative)

    session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{normalized.sha256}",
            artifact_refs={
                "manifest": {"uri": "manifest.json"},
                "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                "field_mapping": {
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {
                                "kind": "CONTENT",
                                "role": "article.body.plain",
                            },
                        }
                    ]
                },
            },
        )
    )
    session.flush()
    MetricRegistryService(session).sync()
    contract = QualityContractService(session).publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "duplicate gate",
                "version_label": "v1",
                "dataset_version_id": version_id,
                "metric_key": "02080103",
                "metric_version": "1.0.0",
                "measurement": "exact_affected_record_ratio",
                "calculator_key": "text_duplicate_degree",
                "calculator_version": "2.0.0",
                "parameters": {"content_role": "article.body.plain"},
                "rule": {
                    "operator": "LTE",
                    "threshold": 0.5,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        )
    )
    session.commit()
    return contract


def test_delete_run_removes_run_and_related_data(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        service.execute(run.run_id)
        session.commit()

        quality = QualityResultService(session, store)
        assert quality.get_report(run.run_id) is not None
        assert len(quality.list_rules(run.run_id)) == 1
        issue_total, _issues = quality.list_issues(run.run_id)
        # Two exact-duplicate facts plus the failed threshold are guaranteed;
        # optional near-duplicate candidate detectors may add further issues.
        assert issue_total >= 3
        quality.rebuild_operational_report(run.run_id)
        session.commit()

        artifact_uris = {
            evidence.artifact.uri
            for result in service.list_results(run.run_id)
            for evidence in result.evidence
        }
        for index in session.scalars(
            select(IssueIndexModel).where(IssueIndexModel.run_id == run.run_id)
        ).all():
            uri = index.document.get("artifact", {}).get("uri")
            if uri:
                artifact_uris.add(uri)
        assert artifact_uris
        for uri in artifact_uris:
            assert store.exists(uri)

        deleted = service.delete_run(run.run_id)
        session.commit()

    assert deleted is True
    with Session(engine) as session:
        assert session.get(EvaluationRunModel, run.run_id) is None
        assert session.get(PhysicalEvaluationPlanModel, run.physical_plan_id) is None
        assert (
            session.scalars(
                select(StageRunModel).where(StageRunModel.run_id == run.run_id)
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(MetricResultModel).where(MetricResultModel.run_id == run.run_id)
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(RuleEvaluationModel).where(
                    RuleEvaluationModel.run_id == run.run_id
                )
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(QualityIssueModel).where(QualityIssueModel.run_id == run.run_id)
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(IssueIndexModel).where(IssueIndexModel.run_id == run.run_id)
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(ScoredQualityReportModel).where(
                    ScoredQualityReportModel.run_id == run.run_id
                )
            ).first()
            is None
        )
        assert (
            session.scalars(
                select(QualityReportModel).where(QualityReportModel.run_id == run.run_id)
            ).first()
            is None
        )

    for uri in artifact_uris:
        assert not store.exists(uri)
    assert not store.exists(f"evaluation-runs/{run.run_id}/")


def test_delete_run_rejects_non_terminal_status(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()

        with pytest.raises(EvaluationRunNotDeletableError) as exc:
            service.delete_run(run.run_id)

        assert exc.value.status == "PENDING"


def test_delete_missing_run_returns_false(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        service = EvaluationRunService(session, store)
        assert service.delete_run(uuid4()) is False


def test_delete_run_does_not_affect_other_runs(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        first = service.create(contract.contract.contract_version_id)
        second = service.create(contract.contract.contract_version_id)
        session.commit()
        service.execute(first.run_id)
        service.execute(second.run_id)
        session.commit()

        quality = QualityResultService(session, store)
        assert quality.get_report(first.run_id) is not None
        assert quality.get_report(second.run_id) is not None

        first_uris = {
            evidence.artifact.uri
            for result in service.list_results(first.run_id)
            for evidence in result.evidence
        }
        second_uris = {
            evidence.artifact.uri
            for result in service.list_results(second.run_id)
            for evidence in result.evidence
        }

        deleted = service.delete_run(first.run_id)
        session.commit()

    assert deleted is True
    with Session(engine) as session:
        assert session.get(EvaluationRunModel, first.run_id) is None
        assert session.get(EvaluationRunModel, second.run_id) is not None
        quality = QualityResultService(session, store)
        assert quality.get_report(second.run_id) is not None

    for uri in first_uris:
        assert not store.exists(uri)
    for uri in second_uris:
        assert store.exists(uri)

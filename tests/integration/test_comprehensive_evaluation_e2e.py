from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from apps.api.routes.evaluation_runs import get_evaluation_dispatcher
from qa_eval.application.services.dataset_version_service import DatasetVersionService
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_profile import (
    SQLAlchemyDatasetProfileRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/e2e-text-quality-baseline.jsonl"


def _engine():
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


def _mapping() -> FieldMappingSpecV2:
    return FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
            "mappings": [
                {
                    "source_path": "$.sample_id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID"},
                    "value_type": "string",
                    "presence_policy": "REJECT_MISSING",
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.text",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.plain",
                        "modality": "text",
                    },
                    "value_type": "string|null",
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
            ],
        }
    )


def test_uploaded_version_agent_to_comprehensive_report_e2e(tmp_path: Path) -> None:
    """P0 Demo：上传版本后经 Agent 人工门禁执行多指标评测并生成报告。"""

    engine = _engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        datasets = SQLAlchemyDatasetRepository(session)
        versions = DatasetVersionService(
            dataset_repository=datasets,
            version_repository=SQLAlchemyDatasetVersionRepository(session),
            profile_repository=SQLAlchemyDatasetProfileRepository(session),
            artifact_store=store,
        )
        dataset = datasets.add(Dataset(name="comprehensive-e2e", modality=["text"]))
        version = versions.create_version(dataset.id, "v1")
        versions.upload_raw_file(version.id, FIXTURE.name, FIXTURE.read_bytes())
        versions.save_field_mapping(version.id, _mapping())
        published = versions.publish_version(version.id)
        MetricRegistryService(session).sync()
        session.commit()

    assert published["counts"] == {"input": 7, "normalized": 7, "rejected": 0}

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    executed: list[UUID] = []

    def execute_now(run_id: UUID) -> None:
        with Session(engine) as session:
            EvaluationRunService(session, store).execute(run_id)
            session.commit()
        executed.append(run_id)

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_artifact_store] = lambda: store
    app.dependency_overrides[get_evaluation_dispatcher] = lambda: execute_now

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version.id)},
        )
        assert created.status_code == 201, created.text
        agent_session_id = created.json()["session_id"]

        recommended = client.post(
            f"/api/v1/agent-sessions/{agent_session_id}/messages",
            json={
                "content": "请为当前数据集推荐综合质量评测方案",
                "client_request_id": str(uuid4()),
            },
        )
        assert recommended.status_code == 201, recommended.text
        recommendation = recommended.json()
        assert [item["tool_name"] for item in recommendation["tool_invocations"]] == [
            "get_dataset_profile",
            "discover_metrics",
            "recommend_evaluation_proposal",
        ]
        assert all(
            item["status"] == "SUCCEEDED"
            for item in recommendation["tool_invocations"]
        )

        snapshot = client.get(f"/api/v1/agent-sessions/{agent_session_id}").json()
        revision = snapshot["active_proposal_revision"]
        assert revision["validation"]["valid"] is False
        proposal = revision["proposal"]
        for item in proposal["items"]:
            if item["rule"]["threshold"] is None:
                item["enabled"] = False
        saved = client.put(
            f"/api/v1/agent-sessions/{agent_session_id}/proposal-revisions/{revision['proposal_revision_id']}",
            json={"proposal": proposal},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["validation"]["valid"] is True

        confirmed = client.post(
            f"/api/v1/agent-sessions/{agent_session_id}/contract-confirmations",
            json={
                "proposal_revision_id": revision["proposal_revision_id"],
                "confirmed": True,
                "name": "文章综合质量 E2E",
                "version_label": "v1",
                "client_request_id": str(uuid4()),
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["item_count"] == 2

        submitted = client.post(
            f"/api/v1/agent-sessions/{agent_session_id}/evaluation-runs",
            json={
                "contract_version_id": confirmed.json()["contract_version_id"],
                "confirmed": True,
                "client_request_id": str(uuid4()),
            },
        )
        assert submitted.status_code == 202, submitted.text
        run_id = UUID(submitted.json()["run_id"])
        assert executed == [run_id]

        observed = client.get(
            f"/api/v1/agent-sessions/{agent_session_id}/evaluation-runs/{run_id}/status"
        )
        results = client.get(f"/api/v1/evaluation-runs/{run_id}/metric-results")
        report_response = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-report"
        )

    assert observed.status_code == 200, observed.text
    assert observed.json()["terminal"] is True
    assert observed.json()["status"] == "SUCCEEDED"
    assert results.status_code == 200, results.text
    assert {item["metric"]["key"] for item in results.json()} == {
        "0204",
        "02080103",
    }
    assert all(item["status"] == "OK" for item in results.json())

    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["schema_version"] == "1.1"
    assert report["run_id"] == str(run_id)
    assert report["execution_status"] == "SUCCEEDED"
    # Registry currently has no approved guide weights or versioned score mapping;
    # measurements must not be promoted to a fabricated comprehensive score.
    assert report["scoring"]["overall_score"] is None
    assert report["scoring"]["evaluated_score"] is None
    assert report["scoring"]["overall_score_status"] == "UNAVAILABLE"
    assert report["scoring"]["effective_coverage"] == 0
    assert report["coverage_analysis"]["effective_scoring"]["ratio"] is None
    assert report["coverage_analysis"]["effective_scoring"]["unavailable_reason"]
    assert {item["metric_key"] for item in report["guide_coverage"]} >= {
        "0204",
        "02080103",
    }
    assert any(item["score"] is None for item in report["radar"])
    assert "不代表指南全指标综合质量" in report["scoring"]["disclaimer"]

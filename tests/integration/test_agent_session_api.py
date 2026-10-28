from collections.abc import Generator
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.agent import (
    ConversationMessageModel,
    ProposalRevisionModel,
    SkillInvocationModel,
    ToolInvocationModel,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.uuid7 import uuid7


def test_agent_session_snapshot_restores_conversation_and_trace():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                artifact_refs={
                    "normalized": {"uri": "normalized.jsonl"},
                    "manifest": {"uri": "manifest.json"},
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
        session.commit()

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        assert created.status_code == 201
        session_id = UUID(created.json()["session_id"])

        with Session(engine) as session:
            session.add_all(
                [
                    ConversationMessageModel(
                        message_id=uuid7(),
                        session_id=session_id,
                        role="USER",
                        content="推荐综合评测方案",
                        attachments=[],
                    ),
                    SkillInvocationModel(
                        invocation_id=uuid7(),
                        session_id=session_id,
                        skill_key="data-quality",
                        skill_version="1.0.0",
                        status="SUCCEEDED",
                        reason="recommendation requested",
                    ),
                    ToolInvocationModel(
                        invocation_id=uuid7(),
                        session_id=session_id,
                        tool_name="recommend_evaluation_proposal",
                        arguments_digest=f"sha256:{'a' * 64}",
                        status="SUCCEEDED",
                        result_refs=[{"kind": "proposal_revision", "revision": 1}],
                    ),
                    ProposalRevisionModel(
                        proposal_revision_id=uuid7(),
                        session_id=session_id,
                        revision=1,
                        proposal={"schema_version": "1.0", "items": []},
                        validation={"valid": True},
                        profile_fingerprint=f"sha256:{'b' * 64}",
                    ),
                ]
            )
            session.commit()

        restored = client.get(f"/api/v1/agent-sessions/{session_id}")

    assert restored.status_code == 200
    body = restored.json()
    assert body["session"]["dataset_version_id"] == str(version_id)
    assert body["messages"][0]["content"] == "推荐综合评测方案"
    assert body["skill_invocations"][0]["skill_key"] == "data-quality"
    assert body["tool_invocations"][0]["status"] == "SUCCEEDED"
    assert body["active_proposal_revision"]["revision"] == 1


def test_agent_session_rejects_unpublished_dataset_version():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="draft", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="DRAFT",
            )
        )
        session.commit()

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )

    assert response.status_code == 409


def test_agent_message_runs_skill_and_real_tools_then_restores_trace():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                artifact_refs={
                    "normalized": {"uri": "normalized.jsonl"},
                    "manifest": {"uri": "manifest.json"},
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
        session.add(
            DatasetProfileModel(
                id=uuid4(),
                dataset_version_id=version_id,
                document={"structure": {"content_roles": ["article.body.plain"]}},
                checksum_sha256="c" * 64,
                profiler_key="base",
                profiler_version="1.0.0",
                config_fingerprint=f"sha256:{'d' * 64}",
            )
        )
        MetricRegistryService(session).sync()
        session.commit()

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        session_id = created.json()["session_id"]
        response = client.post(
            f"/api/v1/agent-sessions/{session_id}/messages",
            json={
                "content": "请为当前数据集推荐综合质量评测方案",
                "client_request_id": str(uuid4()),
            },
        )
        repeated = client.post(
            f"/api/v1/agent-sessions/{session_id}/messages",
            json={
                "content": "请为当前数据集推荐综合质量评测方案",
                "client_request_id": str(uuid4()),
            },
        )
        restored = client.get(f"/api/v1/agent-sessions/{session_id}")

    assert response.status_code == 201
    assert repeated.status_code == 201
    result = response.json()
    assert result["skill_invocation"]["skill_key"] == "data-quality"
    assert result["skill_invocation"]["status"] == "SUCCEEDED", result[
        "skill_invocation"
    ]["reason"]
    assert [item["tool_name"] for item in result["tool_invocations"]] == [
        "get_dataset_profile",
        "discover_metrics",
        "recommend_evaluation_proposal",
    ]
    assert all(item["status"] == "SUCCEEDED" for item in result["tool_invocations"])
    snapshot = restored.json()
    assert len(snapshot["messages"]) == 4
    assert snapshot["session"]["active_skill"] == "data-quality"
    assert len(snapshot["tool_invocations"]) == 6
    proposal = snapshot["active_proposal_revision"]
    assert proposal["revision"] == 2
    assert proposal["validation"]["valid"] is True
    assert [item["metric"]["key"] for item in proposal["proposal"]["items"]] == [
        "0204",
        "02080103",
    ]
    duplicate = proposal["proposal"]["items"][1]
    assert duplicate["selected_calculator"]["key"] == "exact_text_duplicate"
    assert "DEMO_POLICY_V1" in duplicate["limitations"][0]
    first, second = snapshot["proposal_revisions"]
    assert first["proposal"]["items"] == second["proposal"]["items"]

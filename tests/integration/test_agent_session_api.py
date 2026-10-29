from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from apps.api.routes.evaluation_runs import get_evaluation_dispatcher
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.contracts.execution import EvaluationRunV1
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
from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel
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


def test_same_dataset_version_supports_multiple_isolated_sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="multi-session", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
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
        first = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        second = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        first_id = UUID(first.json()["session_id"])
        second_id = UUID(second.json()["session_id"])
        with Session(engine) as session:
            session.add(
                ConversationMessageModel(
                    message_id=uuid7(),
                    session_id=first_id,
                    role="USER",
                    content="only in the first session",
                    attachments=[],
                )
            )
            session.commit()
        listed = client.get(
            "/api/v1/agent-sessions",
            params={"dataset_version_id": str(version_id)},
        )
        first_snapshot = client.get(f"/api/v1/agent-sessions/{first_id}")
        second_snapshot = client.get(f"/api/v1/agent-sessions/{second_id}")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first_id != second_id
    assert listed.status_code == 200
    assert {item["session_id"] for item in listed.json()} == {
        str(first_id),
        str(second_id),
    }
    assert [item["content"] for item in first_snapshot.json()["messages"]] == [
        "only in the first session"
    ]
    assert second_snapshot.json()["messages"] == []


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
    dispatched: list[UUID] = []
    app.dependency_overrides[get_evaluation_dispatcher] = lambda: dispatched.append
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
        revision_id = restored.json()["active_proposal_revision"][
            "proposal_revision_id"
        ]
        proposal = restored.json()["active_proposal_revision"]["proposal"]
        for item in proposal["items"]:
            if item["rule"]["threshold"] is None:
                item["enabled"] = False
        saved = client.put(
            f"/api/v1/agent-sessions/{session_id}/proposal-revisions/{revision_id}",
            json={"proposal": proposal},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["validation"]["valid"] is True, saved.text
        confirmed = client.post(
            f"/api/v1/agent-sessions/{session_id}/contract-confirmations",
            json={
                "proposal_revision_id": revision_id,
                "confirmed": True,
                "name": "综合质量契约",
                "version_label": "proposal-r2",
                "client_request_id": str(uuid4()),
            },
        )
        duplicate_confirmation = client.post(
            f"/api/v1/agent-sessions/{session_id}/contract-confirmations",
            json={
                "proposal_revision_id": revision_id,
                "confirmed": True,
                "name": "重复发布",
                "version_label": "proposal-r2-copy",
                "client_request_id": str(uuid4()),
            },
        )
        contract_version_id = confirmed.json()["contract_version_id"]
        submitted = client.post(
            f"/api/v1/agent-sessions/{session_id}/evaluation-runs",
            json={
                "contract_version_id": contract_version_id,
                "confirmed": True,
                "client_request_id": str(uuid4()),
            },
        )
        run_id = UUID(submitted.json()["run_id"])
        pending_observation = client.get(
            f"/api/v1/agent-sessions/{session_id}/evaluation-runs/{run_id}/status"
        )
        with Session(engine) as session:
            row = session.get(EvaluationRunModel, run_id)
            run = EvaluationRunV1.model_validate(row.document).model_copy(
                update={
                    "status": "PARTIAL_SUCCEEDED",
                    "error": {"phase": "QUALITY_RESULT_FINALIZATION"},
                }
            )
            row.status = "PARTIAL_SUCCEEDED"
            row.document = run.model_dump(mode="json")
            session.commit()
        terminal_observation = client.get(
            f"/api/v1/agent-sessions/{session_id}/evaluation-runs/{run_id}/status"
        )
        repeated_observation = client.get(
            f"/api/v1/agent-sessions/{session_id}/evaluation-runs/{run_id}/status"
        )
        after_confirmation = client.get(f"/api/v1/agent-sessions/{session_id}")

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
    assert proposal["validation"]["valid"] is False
    assert {item["metric"]["key"] for item in proposal["proposal"]["items"]} >= {
        "0201",
        "0202",
        "0203",
        "0204",
        "0205",
        "0206",
        "0207",
        "0208",
        "02080101",
        "02080102",
        "02080103",
        "02080104",
        "02080105",
        "02080106",
        "02080107",
    }
    duplicate = next(
        item
        for item in proposal["proposal"]["items"]
        if item["metric"]["key"] == "02080103"
    )
    assert duplicate["selected_calculator"]["key"] == "exact_text_duplicate"
    assert "DEMO_POLICY_V1" in duplicate["limitations"][0]
    first, second = snapshot["proposal_revisions"]
    assert first["proposal"]["items"] == second["proposal"]["items"]
    assert confirmed.status_code == 201, confirmed.text
    confirmation = confirmed.json()
    assert confirmation["item_count"] == 2
    assert confirmation["tool_invocation"]["tool_name"] == "create_quality_contract"
    assert confirmation["tool_invocation"]["status"] == "SUCCEEDED"
    assert duplicate_confirmation.status_code == 409
    assert submitted.status_code == 202, submitted.text
    assert dispatched == [run_id]
    assert pending_observation.json()["terminal"] is False
    assert terminal_observation.json()["completion_message_created"] is True
    assert terminal_observation.json()["status"] == "PARTIAL_SUCCEEDED"
    assert repeated_observation.json()["completion_message_created"] is False
    restored_confirmation = after_confirmation.json()
    assert restored_confirmation["session"]["status"] == "COMPLETED"
    contract_tools = [
        item
        for item in restored_confirmation["tool_invocations"]
        if item["tool_name"] == "create_quality_contract"
    ]
    assert len(contract_tools) == 1
    completion_messages = [
        item
        for item in restored_confirmation["messages"]
        if any(
            attachment["type"] == "EVALUATION_RUN_COMPLETED"
            for attachment in item["attachments"]
        )
    ]
    assert len(completion_messages) == 1
    contract_message = next(
        item
        for item in restored_confirmation["messages"]
        if any(
            attachment["type"] == "QUALITY_CONTRACT"
            for attachment in item["attachments"]
        )
    )
    attachment_types = {
        attachment["type"] for attachment in contract_message["attachments"]
    }
    assert attachment_types == {"HUMAN_CONFIRMATION", "QUALITY_CONTRACT"}


def test_contract_confirmation_requires_literal_confirmation():
    from pydantic import ValidationError

    from qa_eval.application.agent.contracts import ContractConfirmationCreate

    with pytest.raises(ValidationError):
        ContractConfirmationCreate.model_validate(
            {
                "proposal_revision_id": str(uuid4()),
                "confirmed": False,
                "name": "not confirmed",
                "version_label": "v1",
                "client_request_id": str(uuid4()),
            }
        )

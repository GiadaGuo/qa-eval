from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
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
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    PhysicalEvaluationPlanModel,
)
from qa_eval.infrastructure.db.models.quality_contract import QualityContractVersionModel
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
        initial_validation = restored.json()["active_proposal_revision"]["validation"]
        by_metric = {item["metric"]["key"]: item for item in proposal["items"]}
        assert "content_role" not in by_metric["0201"]["parameters"]
        assert "content_role" not in by_metric["0203"]["parameters"]
        validation_by_key = {item["key"]: item for item in initial_validation["items"]}
        type_findings = validation_by_key[by_metric["0207"]["key"]]["findings"]
        assert [item["code"] for item in type_findings].count(
            "REQUIRED_PARAMETER_MISSING"
        ) == 1
        assert "MISSING_STATIC_CONTEXT" not in {
            item["code"] for item in type_findings
        }
        declared_type_finding = next(
            item
            for item in type_findings
            if item["code"] == "REQUIRED_PARAMETER_MISSING"
        )
        assert declared_type_finding["message"] == "缺少数据集声明类型"
        assert declared_type_finding["field_path"] == (
            "calculator_parameters.declared_dataset_type"
        )
        assert declared_type_finding["action"] == "EDIT_CALCULATOR_PARAMETERS"

        for item in proposal["items"]:
            if item["rule"]["threshold"] is None:
                item["enabled"] = False
        type_item = by_metric["0207"]
        type_item["enabled"] = True
        type_item["parameters"]["declared_dataset_type"] = "INDUSTRY_GENERAL"
        type_item["rule"]["threshold"] = 0.8
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
            contract_row = session.get(
                QualityContractVersionModel, UUID(contract_version_id)
            )
            type_contract_item = next(
                item
                for item in contract_row.document["items"]
                if item["metric"]["key"] == "0207"
            )
            assert type_contract_item["calculation"]["parameters"][
                "declared_dataset_type"
            ] == "INDUSTRY_GENERAL"
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
    assert confirmation["item_count"] == 3
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


def _fk_enforcing_engine():
    """SQLite 默认不启用外键，开启后与 PostgreSQL 的 CASCADE 行为一致。"""
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


def _seed_published_version(engine, name: str) -> UUID:
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name=name, modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
            )
        )
        session.commit()
    return version_id


def _override_app(engine):
    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    return app


def _seed_run_asset(engine, version_id: UUID, status: str) -> tuple[UUID, UUID]:
    """插入与 AgentSession 无外键关联的已发布 Contract 与 EvaluationRun。"""
    contract_version_id, physical_plan_id, run_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with Session(engine) as session:
        # 无 relationship 时 flush 按表名排序，需逐个落库满足外键顺序
        session.add(
            QualityContractVersionModel(
                contract_version_id=contract_version_id,
                contract_id=uuid4(),
                target_dataset_version_id=version_id,
                version_label="proposal-r1",
                name="综合质量契约",
                status="PUBLISHED",
                fingerprint=f"sha256:{'e' * 64}",
                document={"items": []},
                published_at=now,
            )
        )
        session.flush()
        session.add(
            PhysicalEvaluationPlanModel(
                physical_plan_id=physical_plan_id,
                dataset_version_id=version_id,
                contract_version_id=contract_version_id,
                semantic_fingerprint=f"sha256:{'f' * 64}",
                document={"stages": []},
            )
        )
        session.flush()
        session.add(
            EvaluationRunModel(
                run_id=run_id,
                physical_plan_id=physical_plan_id,
                dataset_version_id=version_id,
                contract_version_id=contract_version_id,
                status=status,
                document={"run_id": str(run_id)},
            )
        )
        session.commit()
    return contract_version_id, run_id


def test_delete_agent_session_removes_children_and_keeps_published_assets():
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    version_a = _seed_published_version(engine, "delete-target")
    version_b = _seed_published_version(engine, "other-version")
    contract_version_id, run_id = _seed_run_asset(engine, version_a, "SUCCEEDED")
    app = _override_app(engine)

    with TestClient(app) as client:
        target = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_a)},
        )
        other = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_b)},
        )
        target_id = UUID(target.json()["session_id"])
        other_id = UUID(other.json()["session_id"])

        with Session(engine) as session:
            session.add_all(
                [
                    ConversationMessageModel(
                        message_id=uuid7(),
                        session_id=target_id,
                        role="ASSISTANT",
                        content="已提交 Evaluation Run",
                        attachments=[
                            {
                                "type": "EVALUATION_RUN",
                                "run_id": str(run_id),
                                "status": "SUCCEEDED",
                            }
                        ],
                    ),
                    SkillInvocationModel(
                        invocation_id=uuid7(),
                        session_id=target_id,
                        skill_key="data-quality",
                        skill_version="1.0.0",
                        status="SUCCEEDED",
                    ),
                    ToolInvocationModel(
                        invocation_id=uuid7(),
                        session_id=target_id,
                        tool_name="create_evaluation_run",
                        arguments_digest=f"sha256:{'a' * 64}",
                        status="SUCCEEDED",
                        result_refs=[],
                    ),
                    ProposalRevisionModel(
                        proposal_revision_id=uuid7(),
                        session_id=target_id,
                        revision=1,
                        proposal={"schema_version": "1.0", "items": []},
                        validation={"valid": True},
                        profile_fingerprint=f"sha256:{'b' * 64}",
                    ),
                ]
            )
            session.commit()

        deleted = client.delete(f"/api/v1/agent-sessions/{target_id}")
        repeat_delete = client.delete(f"/api/v1/agent-sessions/{target_id}")
        restored = client.get(f"/api/v1/agent-sessions/{target_id}")
        listed_a = client.get(
            "/api/v1/agent-sessions",
            params={"dataset_version_id": str(version_a)},
        )
        listed_b = client.get(
            "/api/v1/agent-sessions",
            params={"dataset_version_id": str(version_b)},
        )

    assert deleted.status_code == 204
    assert repeat_delete.status_code == 404
    assert restored.status_code == 404
    assert listed_a.json() == []
    assert {item["session_id"] for item in listed_b.json()} == {str(other_id)}

    with Session(engine) as session:
        for model in (
            ConversationMessageModel,
            SkillInvocationModel,
            ToolInvocationModel,
            ProposalRevisionModel,
        ):
            rows = session.scalars(
                select(model).where(model.session_id == target_id)
            ).all()
            assert rows == []
        assert session.get(QualityContractVersionModel, contract_version_id) is not None
        assert session.get(EvaluationRunModel, run_id) is not None


def test_delete_agent_session_blocked_while_evaluation_run_active():
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    version_id = _seed_published_version(engine, "running-run")
    _, run_id = _seed_run_asset(engine, version_id, "RUNNING")
    app = _override_app(engine)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        session_id = UUID(created.json()["session_id"])
        with Session(engine) as session:
            session.add(
                ConversationMessageModel(
                    message_id=uuid7(),
                    session_id=session_id,
                    role="ASSISTANT",
                    content="已提交 Evaluation Run",
                    attachments=[
                        {
                            "type": "EVALUATION_RUN",
                            "run_id": str(run_id),
                            "status": "RUNNING",
                        }
                    ],
                )
            )
            session.commit()

        blocked = client.delete(f"/api/v1/agent-sessions/{session_id}")
        still_there = client.get(f"/api/v1/agent-sessions/{session_id}")

    assert blocked.status_code == 409
    assert "正在执行" in blocked.json()["detail"]["message"]
    assert still_there.status_code == 200


def test_delete_agent_session_blocked_while_tool_invocation_running():
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    version_id = _seed_published_version(engine, "running-tool")
    app = _override_app(engine)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agent-sessions",
            json={"dataset_version_id": str(version_id)},
        )
        session_id = UUID(created.json()["session_id"])
        with Session(engine) as session:
            session.add(
                ToolInvocationModel(
                    invocation_id=uuid7(),
                    session_id=session_id,
                    tool_name="recommend_evaluation_proposal",
                    arguments_digest=f"sha256:{'c' * 64}",
                    status="RUNNING",
                    result_refs=[],
                )
            )
            session.commit()

        blocked = client.delete(f"/api/v1/agent-sessions/{session_id}")

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "AGENT_SESSION_NOT_DELETABLE"


def test_delete_missing_agent_session_returns_404():
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    app = _override_app(engine)

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/agent-sessions/{uuid4()}")

    assert response.status_code == 404

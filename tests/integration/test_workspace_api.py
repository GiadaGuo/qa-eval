from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel
from qa_eval.infrastructure.db.session import get_db


def test_workspace_isolation_drafts_and_invalid_records(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_EVAL_WEB_ACCOUNTS_DB", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("QA_EVAL_WEB_ADMIN_PASSWORD", "workspace-admin")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="Articles", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id, dataset_id=dataset_id, version="v1", status="PUBLISHED"
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    web = "/api/web"
    workspace = web + "/workspace"
    draft_path = f"{workspace}/drafts/manual/{version_id}"
    draft = {"name": "Saved draft", "selected": {}, "excluded": {}}
    admin = {"username": "admin", "password": "workspace-admin"}
    operator = {"username": "operator", "password": "workspace-operator"}
    checkpoint = {"kind": "manual", "object_id": str(version_id)}

    assert client.get(workspace).status_code == 401
    assert client.post(web + "/session", json=admin).status_code == 200
    assert client.get(workspace).json()["counts"] == {
        "datasets": 1,
        "pending": 0,
        "running": 0,
        "reports": 0,
    }
    assert client.get(workspace).json()["items"] == []
    assert client.put(workspace + "/checkpoints", json=checkpoint).status_code == 200
    assert client.get(draft_path).json() is None
    assert client.put(draft_path, json=draft).status_code == 200
    assert len(client.get(workspace).json()["items"]) == 1
    restore_path = f"{workspace}/checkpoints/manual/{version_id}"
    assert client.get(restore_path).json()["dataset_id"] == str(dataset_id)

    assert (
        client.post(
            web + "/accounts", json={**operator, "role": "operator"}
        ).status_code
        == 201
    )
    assert client.delete(web + "/session").status_code == 200
    assert client.post(web + "/session", json=operator).status_code == 200
    assert client.get(draft_path).json() is None
    assert client.get(workspace).json()["items"] == []
    assert client.get(restore_path).status_code == 404
    assert client.post(web + "/session", json=admin).status_code == 200
    assert client.get(draft_path).json() == draft

    operator_draft = {
        "version_id": str(version_id),
        "metric": {"key": "02080104", "version": "1.0.0"},
        "calculator": {"key": "text_completeness", "version": "1.1.0"},
        "values": {"name": "草稿"},
    }
    write_operator = f"{workspace}/operator-drafts"
    first = client.put(write_operator, json=operator_draft)
    assert first.status_code == 200
    draft_id = first.json()["draft_id"]
    operator_draft["values"]["name"] = "新名称"
    assert (
        client.put(write_operator, json=operator_draft).json()["draft_id"] == draft_id
    )
    assert client.get(f"{write_operator}/{draft_id}").json()["values"] == {
        "name": "新名称"
    }
    assert client.get(workspace).json()["items"][0]["kind"] == "operator"
    assert (
        client.delete(f"{workspace}/checkpoints/operator/{draft_id}").status_code == 200
    )

    run_id = uuid4()
    with Session(engine) as session:
        session.add(
            EvaluationRunModel(
                run_id=run_id,
                dataset_version_id=version_id,
                contract_version_id=uuid4(),
                physical_plan_id=uuid4(),
                status="PENDING",
                document={"task_type": "DATASET"},
            )
        )
        session.commit()
    assert (
        client.put(
            workspace + "/checkpoints",
            json={
                "kind": "run",
                "object_id": str(run_id),
            },
        ).status_code
        == 200
    )
    pending = client.get(workspace).json()
    assert pending["counts"]["pending"] == 1
    assert pending["items"][0]["status"] == "PENDING"
    with Session(engine) as session:
        session.get(EvaluationRunModel, run_id).status = "RUNNING"
        session.commit()
    running = client.get(workspace).json()
    assert running["counts"]["pending"] == 0
    assert running["counts"]["running"] == 1
    assert running["items"][0]["status"] == "RUNNING"
    assert client.delete(f"{workspace}/checkpoints/run/{run_id}").status_code == 200

    with Session(engine) as session:
        session.delete(session.get(DatasetVersionModel, version_id))
        session.commit()
    assert client.get(restore_path).status_code == 404
    assert client.get(workspace).json()["items"][0]["valid"] is False
    assert client.delete(restore_path).status_code == 200
    assert client.get(workspace).json()["items"] == []

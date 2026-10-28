# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# EvaluationRun 删除 API：状态码映射与冲突响应结构。
# ====================================================

from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import get_evaluation_run_service
from apps.api.main import create_app
from qa_eval.domain.exceptions import EvaluationRunNotDeletableError


def test_delete_evaluation_run_returns_204() -> None:
    run_id = uuid4()
    service = Mock()
    service.delete_run.return_value = True
    app = create_app()
    app.dependency_overrides[get_evaluation_run_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/evaluation-runs/{run_id}")

    assert response.status_code == 204
    assert response.content == b""
    service.delete_run.assert_called_once_with(run_id)


def test_delete_missing_evaluation_run_returns_404() -> None:
    service = Mock()
    service.delete_run.return_value = False
    app = create_app()
    app.dependency_overrides[get_evaluation_run_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/evaluation-runs/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "EvaluationRun not found"


def test_delete_non_terminal_evaluation_run_returns_409() -> None:
    run_id = uuid4()
    service = Mock()
    service.delete_run.side_effect = EvaluationRunNotDeletableError(
        status="RUNNING",
        message="评测任务状态为 RUNNING，未到达终态，无法删除",
    )
    app = create_app()
    app.dependency_overrides[get_evaluation_run_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/evaluation-runs/{run_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "EVALUATION_RUN_NOT_DELETABLE"
    assert detail["status"] == "RUNNING"
    assert "RUNNING" in detail["message"]

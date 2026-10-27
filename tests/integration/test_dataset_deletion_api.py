# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 数据集 / 数据集版本删除 API：状态码映射与 409 依赖冲突结构。
# ====================================================

from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import (
    get_dataset_service,
    get_dataset_version_service,
)
from apps.api.main import create_app
from qa_eval.domain.exceptions import DatasetVersionInUseError


def _client() -> TestClient:
    return TestClient(create_app())


def test_delete_dataset_returns_204() -> None:
    dataset_id = uuid4()
    service = Mock()
    service.delete_dataset.return_value = True
    app = create_app()
    app.dependency_overrides[get_dataset_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/datasets/{dataset_id}")

    assert response.status_code == 204
    assert response.content == b""
    service.delete_dataset.assert_called_once_with(dataset_id)


def test_delete_missing_dataset_returns_404() -> None:
    service = Mock()
    service.delete_dataset.return_value = False
    app = create_app()
    app.dependency_overrides[get_dataset_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/datasets/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Dataset not found"


def test_delete_dataset_with_protected_versions_returns_409() -> None:
    service = Mock()
    service.delete_dataset.side_effect = DatasetVersionInUseError(
        {"evaluation_runs": 3, "protected_versions": 2},
        code="DATASET_IN_USE",
    )
    app = create_app()
    app.dependency_overrides[get_dataset_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(f"/api/v1/datasets/{uuid4()}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "DATASET_IN_USE"
    assert detail["dependencies"] == {
        "evaluation_runs": 3,
        "protected_versions": 2,
    }


def test_delete_dataset_version_returns_204() -> None:
    dataset_id = uuid4()
    version_id = uuid4()
    service = Mock()
    service.delete_version.return_value = True
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/datasets/{dataset_id}/versions/{version_id}"
        )

    assert response.status_code == 204
    assert response.content == b""
    service.delete_version.assert_called_once_with(dataset_id, version_id)


def test_delete_missing_dataset_version_returns_404() -> None:
    service = Mock()
    service.delete_version.return_value = False
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/datasets/{uuid4()}/versions/{uuid4()}"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "DatasetVersion not found"


def test_delete_version_of_other_dataset_returns_404() -> None:
    service = Mock()
    service.delete_version.side_effect = ValueError(
        "DatasetVersion does not belong to dataset"
    )
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/datasets/{uuid4()}/versions/{uuid4()}"
        )

    assert response.status_code == 404


def test_delete_version_with_evaluation_dependency_returns_409() -> None:
    service = Mock()
    service.delete_version.side_effect = DatasetVersionInUseError(
        {"evaluation_runs": 3}
    )
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/datasets/{uuid4()}/versions/{uuid4()}"
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "DATASET_VERSION_IN_USE"
    assert detail["dependencies"] == {"evaluation_runs": 3}

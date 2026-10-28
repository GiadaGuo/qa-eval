# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档版本 API：上传 201、非法输入 422、409/404 状态码映射与下载流。
# ====================================================

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import get_document_version_service
from apps.api.main import create_app
from qa_eval.domain.entities.document_version import DocumentVersion
from qa_eval.domain.exceptions import DocumentVersionInUseError

CONTENT = b"# dataset readme\n"


def _document(**overrides) -> DocumentVersion:
    fields = {
        "id": uuid4(),
        "dataset_id": uuid4(),
        "version": "v1",
        "filename": "readme.md",
        "format": "markdown",
        "size_bytes": len(CONTENT),
        "sha256": "a" * 64,
        "storage_uri": "datasets/x/documents/y/readme.md",
        "created_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return DocumentVersion(**fields)


def _client(service: Mock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_document_version_service] = lambda: service
    return TestClient(app)


def test_upload_returns_201_and_passes_file_to_service() -> None:
    dataset_id = uuid4()
    service = Mock()
    service.upload_document.return_value = _document(dataset_id=dataset_id)

    with _client(service) as client:
        response = client.post(
            f"/api/v1/datasets/{dataset_id}/document-versions",
            files={"file": ("readme.md", CONTENT, "text/markdown")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == "v1"
    assert body["filename"] == "readme.md"
    assert body["sha256"] == "a" * 64
    service.upload_document.assert_called_once_with(dataset_id, "readme.md", CONTENT)


def test_upload_rejects_non_markdown_extension_with_422() -> None:
    service = Mock()

    with _client(service) as client:
        response = client.post(
            f"/api/v1/datasets/{uuid4()}/document-versions",
            files={"file": ("readme.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 422
    assert "Markdown" in response.json()["detail"]
    service.upload_document.assert_not_called()


def test_upload_maps_validation_error_to_422() -> None:
    service = Mock()
    service.upload_document.side_effect = ValueError("说明文档内容为空")

    with _client(service) as client:
        response = client.post(
            f"/api/v1/datasets/{uuid4()}/document-versions",
            files={"file": ("readme.md", b"", "text/markdown")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "说明文档内容为空"


def test_upload_maps_missing_dataset_to_404() -> None:
    service = Mock()
    service.upload_document.side_effect = LookupError("Dataset not found")

    with _client(service) as client:
        response = client.post(
            f"/api/v1/datasets/{uuid4()}/document-versions",
            files={"file": ("readme.md", CONTENT, "text/markdown")},
        )

    assert response.status_code == 404


def test_list_documents_maps_entities() -> None:
    dataset_id = uuid4()
    service = Mock()
    service.list_documents.return_value = [
        _document(dataset_id=dataset_id, version="v2"),
        _document(dataset_id=dataset_id, version="v1"),
    ]

    with _client(service) as client:
        response = client.get(f"/api/v1/datasets/{dataset_id}/document-versions")

    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == ["v2", "v1"]


def test_metadata_returns_document_details() -> None:
    document_id = uuid4()
    service = Mock()
    service.get_document_metadata.return_value = {
        "id": str(document_id),
        "version": "v1",
        "filename": "readme.md",
        "sha256": "a" * 64,
        "line_count": 3,
        "parse_status": "NOT_PARSED",
    }

    with _client(service) as client:
        response = client.get(f"/api/v1/document-versions/{document_id}")

    assert response.status_code == 200
    assert response.json()["line_count"] == 3


def test_metadata_missing_document_returns_404() -> None:
    service = Mock()
    service.get_document_metadata.return_value = None

    with _client(service) as client:
        response = client.get(f"/api/v1/document-versions/{uuid4()}")

    assert response.status_code == 404


def test_download_streams_original_bytes() -> None:
    document_id = uuid4()
    service = Mock()
    service.download_document.return_value = (_document(id=document_id), CONTENT)

    with _client(service) as client:
        response = client.get(f"/api/v1/document-versions/{document_id}/download")

    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-type"].startswith("text/markdown")
    assert "readme.md" in response.headers["content-disposition"]


def test_download_missing_artifact_returns_404() -> None:
    service = Mock()
    service.download_document.side_effect = FileNotFoundError("artifact not found")

    with _client(service) as client:
        response = client.get(f"/api/v1/document-versions/{uuid4()}/download")

    assert response.status_code == 404


def test_delete_returns_204() -> None:
    service = Mock()
    service.delete_document.return_value = True

    with _client(service) as client:
        response = client.delete(f"/api/v1/document-versions/{uuid4()}")

    assert response.status_code == 204
    assert response.content == b""


def test_delete_missing_document_returns_404() -> None:
    service = Mock()
    service.delete_document.return_value = False

    with _client(service) as client:
        response = client.delete(f"/api/v1/document-versions/{uuid4()}")

    assert response.status_code == 404


def test_delete_with_report_task_reference_returns_409() -> None:
    service = Mock()
    service.delete_document.side_effect = DocumentVersionInUseError(
        {"document_report_tasks": 3}
    )

    with _client(service) as client:
        response = client.delete(f"/api/v1/document-versions/{uuid4()}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "DOCUMENT_VERSION_IN_USE"
    assert detail["dependencies"] == {"document_report_tasks": 3}

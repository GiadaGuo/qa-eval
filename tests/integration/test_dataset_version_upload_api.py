from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import get_dataset_version_service
from apps.api.main import create_app


def test_multipart_upload_passes_selected_file_to_service() -> None:
    version_id = uuid4()
    service = Mock()
    service.upload_raw_file.return_value = {
        "version_id": str(version_id),
        "filename": "records.jsonl",
        "uri": "datasets/example/raw/records.jsonl",
        "size_bytes": 36,
        "sha256": "a" * 64,
        "validated_format": "jhsjk_article_snapshot_v1",
    }
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    content = b'{"sample_id":"a","text":"hello"}\n'
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/dataset-versions/{version_id}/files",
            files={"file": ("records.jsonl", content, "application/x-ndjson")},
        )

    assert response.status_code == 200
    service.upload_raw_file.assert_called_once_with(
        version_id, "records.jsonl", content
    )


def test_source_inspection_reads_uploaded_version_artifact() -> None:
    version_id = uuid4()
    service = Mock()
    service.inspect_uploaded_source.return_value = {
        "source_type": "uploaded_jsonl",
        "filename": "records.jsonl",
        "size_bytes": 36,
        "record_count": 1,
        "fields": [
            {
                "name": "text",
                "data_type": "string",
                "nullable": False,
                "is_primary_key": False,
            }
        ],
        "rows": [{"text": "hello"}],
    }
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: service

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/dataset-versions/{version_id}/source-inspection",
            params={"limit": 10, "max_text_length": 120},
        )

    assert response.status_code == 200
    assert response.json()["filename"] == "records.jsonl"
    service.inspect_uploaded_source.assert_called_once_with(
        version_id,
        preview_limit=10,
        max_text_length=120,
    )

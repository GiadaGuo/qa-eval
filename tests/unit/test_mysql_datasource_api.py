
from unittest.mock import MagicMock

import mysql.connector
from fastapi.testclient import TestClient

from apps.api.dependencies import get_mysql_reader
from apps.api.main import create_app


def test_mysql_schema_api():
    app = create_app()

    reader = MagicMock()
    reader.get_schema.return_value = {
        "database": "qa_eval_demo",
        "table": "jhsjk_article",
        "row_count": 20,
        "fields": [
            {
                "name": "id",
                "data_type": "char",
                "nullable": False,
                "is_primary_key": True,
            }
        ],
    }

    app.dependency_overrides[get_mysql_reader] = (
        lambda: reader
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/datasources/mysql/schema"
        )

    assert response.status_code == 200
    assert response.json()["row_count"] == 20
    assert response.json()["fields"][0]["name"] == "id"

    reader.get_schema.assert_called_once_with()


def test_mysql_preview_api():
    app = create_app()

    reader = MagicMock()
    reader.get_preview.return_value = {
        "database": "qa_eval_demo",
        "table": "jhsjk_article",
        "limit": 5,
        "rows": [
            {
                "id": "abc123",
                "title": "测试文章",
                "content": "<p>测试正文</p>",
            }
        ],
    }

    app.dependency_overrides[get_mysql_reader] = (
        lambda: reader
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/datasources/mysql/preview"
        )

    assert response.status_code == 200
    assert response.json()["rows"][0]["title"] == "测试文章"

    reader.get_preview.assert_called_once_with(
        limit=5,
        max_text_length=300,
    )


def test_mysql_preview_rejects_invalid_limit():
    app = create_app()

    reader = MagicMock()

    app.dependency_overrides[get_mysql_reader] = (
        lambda: reader
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/datasources/mysql/preview?limit=21"
        )

    assert response.status_code == 422
    reader.get_preview.assert_not_called()


def test_mysql_schema_returns_safe_error():
    app = create_app()

    reader = MagicMock()
    reader.get_schema.side_effect = (
        mysql.connector.Error("private database details")
    )

    app.dependency_overrides[get_mysql_reader] = (
        lambda: reader
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/datasources/mysql/schema"
        )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Failed to read MySQL schema"
    )
    assert "private database details" not in response.text

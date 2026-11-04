# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 13:17:55
# @Description: 
# ====================================================

from fastapi.testclient import TestClient

from apps.api.main import create_app
from qa_eval.infrastructure.db.session import engine


def test_health_check():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "qa-eval-api"
    assert body["database"] == {
        "dialect": engine.dialect.name,
        "writable": True,
    }
    assert body["artifact_store"] == {
        "type": "local",
        "root": "data/artifacts",
        "writable": True,
    }


def test_openapi_available():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200

    schema = response.json()
    assert "/api/v1/health" in schema["paths"]

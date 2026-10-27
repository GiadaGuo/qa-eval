# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description: 闭环 1：LOCAL_VALIDATION API 只读测试。
# ====================================================

from fastapi.testclient import TestClient

from apps.api.main import create_app

LOCAL_VALIDATION_PREFIX = "/api/v1/local-validation"


def _client():
    app = create_app()
    return TestClient(app)


def test_local_validation_summary_returns_local_authority():
    client = _client()
    response = client.get(LOCAL_VALIDATION_PREFIX)

    assert response.status_code == 200
    data = response.json()
    assert data["authority"] == "LOCAL_VALIDATION"
    assert data["not_a_platform_run"] is True
    assert data["dataset_name"] == "policy.jhsjk_article"
    assert data["metric_key"] == "02080103"
    assert data["gate_status"] == "PASS"
    assert data["issue_summary"]["confirmed_count"] == 45
    assert data["issue_summary"]["candidate_count"] == 7517


def test_local_validation_report_returns_quality_report():
    client = _client()
    response = client.get(f"{LOCAL_VALIDATION_PREFIX}/report")

    assert response.status_code == 200
    data = response.json()
    assert data["authority"] == "LOCAL_VALIDATION"
    assert data["not_a_platform_run"] is True
    assert data["report"]["schema_version"] == "1.0"
    assert data["report"]["gate_status"] == "PASS"
    assert data["report"]["execution_status"] == "SUCCEEDED"


def test_local_validation_issues_pagination_and_filter():
    client = _client()
    response = client.get(
        f"{LOCAL_VALIDATION_PREFIX}/issues",
        params={"determination": "CONFIRMED", "limit": 10, "offset": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["authority"] == "LOCAL_VALIDATION"
    assert data["total"] == 45
    assert len(data["issues"]) == 10
    assert all(item["determination"] == "CONFIRMED" for item in data["issues"])


def test_local_validation_manifest_metric_result_contract_endpoints():
    client = _client()

    for endpoint in [
        "/manifest",
        "/metric-result",
        "/contract",
        "/rule-evaluations",
        "/issue-index",
    ]:
        response = client.get(f"{LOCAL_VALIDATION_PREFIX}{endpoint}")
        assert response.status_code == 200, endpoint
        data = response.json()
        assert data["authority"] == "LOCAL_VALIDATION"
        assert data["not_a_platform_run"] is True

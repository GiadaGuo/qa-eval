# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# P2 验收（API 层，mock 服务）：创建任务 202、查询任务、
# 查询报告 200/404、LLM 未配置返回 503。
# ====================================================

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import (
    get_document_llm_client,
    get_document_report_task_service,
    get_settings,
)
from apps.api.main import create_app

REPORT_ID = uuid4()
TASK_ID = uuid4()
DOCUMENT_VERSION_ID = uuid4()


def _task_view(**overrides) -> dict:
    now = datetime.now(UTC).isoformat()
    view = {
        "task_id": str(TASK_ID),
        "document_version_id": str(DOCUMENT_VERSION_ID),
        "dataset_id": str(uuid4()),
        "status": "SUCCEEDED",
        "error": None,
        "report_id": str(REPORT_ID),
        "metrics": [
            {"metric_code": "0101", "metric_name": "基本信息完整性", "status": "OK"}
        ],
        "llm_calls": 4,
        "evaluator_version": "1.0.0",
        "standard_version": "1.0.0",
        "standard_sha256": "a" * 64,
        "created_at": now,
        "started_at": now,
        "finished_at": now,
    }
    view.update(overrides)
    return view


def _client(
    service: Mock,
    *,
    llm_configured: bool = True,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_document_report_task_service] = lambda: service
    if llm_configured:
        app.dependency_overrides[get_document_llm_client] = lambda: Mock()
    else:
        app.dependency_overrides[get_document_llm_client] = None  # 保持依赖默认行为
    # 避免 lifespan 扫到真实数据库
    app.dependency_overrides[get_settings] = lambda: Mock(document_executor_workers=2)
    return TestClient(app)


def test_create_task_returns_202_and_dispatches():
    service = Mock()
    service.create_task.return_value = _task_view(status="PENDING")

    with _client(service) as client:
        response = client.post(
            "/api/v1/document-report-tasks",
            json={"document_version_id": str(DOCUMENT_VERSION_ID)},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == str(TASK_ID)
    assert body["status"] == "PENDING"
    service.create_task.assert_called_once_with(DOCUMENT_VERSION_ID)
    service.session.commit.assert_called()


def test_create_task_404_when_document_version_missing():
    service = Mock()
    service.create_task.side_effect = LookupError("DocumentVersion not found")

    with _client(service) as client:
        response = client.post(
            "/api/v1/document-report-tasks",
            json={"document_version_id": str(DOCUMENT_VERSION_ID)},
        )

    assert response.status_code == 404


def test_create_task_422_on_service_validation_error():
    service = Mock()
    service.create_task.side_effect = ValueError("bad request")

    with _client(service) as client:
        response = client.post(
            "/api/v1/document-report-tasks",
            json={"document_version_id": str(DOCUMENT_VERSION_ID)},
        )

    assert response.status_code == 422


def test_list_tasks_and_get_task():
    service = Mock()
    service.list_tasks.return_value = [
        _task_view(),
    ]

    def get_task(task_id):
        if task_id == TASK_ID:
            return _task_view()
        return None

    service.get_task.side_effect = get_task

    with _client(service) as client:
        list_resp = client.get(
            "/api/v1/document-report-tasks",
            params={"document_version_id": str(DOCUMENT_VERSION_ID)},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        get_resp = client.get(f"/api/v1/document-report-tasks/{TASK_ID}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == str(TASK_ID)

        missing = client.get(f"/api/v1/document-report-tasks/{uuid4()}")
        assert missing.status_code == 404


def test_get_report_200_or_404():
    from qa_eval.contracts.document import DocumentQualityReportV1

    service = Mock()
    service.get_report.return_value = DocumentQualityReportV1.model_validate(
        {
            "schema_version": "1.0",
            "report_id": REPORT_ID,
            "task_id": TASK_ID,
            "document_version_id": DOCUMENT_VERSION_ID,
            "dataset_id": uuid4(),
            "document": {
                "version": "v1",
                "filename": "readme.md",
                "sha256": "a" * 64,
                "line_count": 10,
                "size_bytes": 1024,
            },
            "evaluation_standard": {
                "name": "标准",
                "version": "1.0.0",
                "sha256": "a" * 64,
            },
            "evaluator": {
                "version": "1.0.0",
                "model": "mock-model",
                "prompt_version": "1.0.0",
                "parameters": {},
            },
            "execution_status": "SUCCEEDED",
            "metrics": [
                {
                    "metric": {"key": "0101", "version": "1.0.0"},
                    "metric_name": "基本信息完整性",
                    "status": "OK",
                    "observations": [
                        {
                            "key": "score",
                            "value": 100.0,
                            "value_type": "NUMBER",
                            "unit": "percent",
                            "numerator": 5,
                            "denominator": 5,
                        }
                    ],
                    "items": [
                        {
                            "item_code": "0101-01",
                            "item_name": "数据规模",
                            "status": "SATISFIED",
                            "document_evidence": [
                                {
                                    "line_start": 1,
                                    "line_end": 1,
                                    "excerpt": "# 数据集说明",
                                }
                            ],
                            "inspected_scope": "全文",
                            "reason": "达标",
                            "suggestion": "无需修改",
                            "fixability": "AUTO_FIXABLE",
                        }
                    ],
                }
            ],
            "overall": {
                "score": 100.0,
                "scoring_policy": "等权平均",
            },
            "generated_at": datetime.now(UTC),
        }
    )

    def get_report(report_id):
        return service.get_report.return_value if report_id == REPORT_ID else None

    service.get_report.side_effect = get_report

    with _client(service) as client:
        ok = client.get(f"/api/v1/document-quality-reports/{REPORT_ID}")
        assert ok.status_code == 200
        assert ok.json()["report_id"] == str(REPORT_ID)

        missing = client.get(f"/api/v1/document-quality-reports/{uuid4()}")
        assert missing.status_code == 404


def test_create_task_503_when_llm_unconfigured():
    service = Mock()
    service.create_task.return_value = _task_view(status="PENDING")

    def raise_503():
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="LLM not configured")

    app = create_app()
    app.dependency_overrides[get_document_report_task_service] = lambda: service
    app.dependency_overrides[get_document_llm_client] = raise_503
    app.dependency_overrides[get_settings] = lambda: Mock(document_executor_workers=2)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/document-report-tasks",
            json={"document_version_id": str(DOCUMENT_VERSION_ID)},
        )

    assert response.status_code == 503
    service.create_task.assert_not_called()

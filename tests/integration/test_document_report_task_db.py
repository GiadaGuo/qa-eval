# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# P2 验收（DB 层，mock LLM）：任务执行全链路——解析落盘、
# 逐指标评测、报告落库、分数恒等于复算；部分 ERROR、全部
# ERROR、重试耗尽与启动清扫。
# ====================================================

import re
from uuid import UUID, uuid4

from qa_eval.application.document_evaluation.rubric import REPO_ROOT
from qa_eval.application.services.document_report_task_service import (
    DocumentReportTaskService,
)
from qa_eval.application.services.document_version_service import (
    DocumentVersionService,
)
from qa_eval.config import Settings
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.document_quality_report import (
    DocumentQualityReportModel,
)
from qa_eval.infrastructure.db.models.document_version import (
    DocumentReportTaskModel,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_document_version import (
    SQLAlchemyDocumentVersionRepository,
)
from qa_eval.infrastructure.llm.client import LLMTransportError
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

CONTENT = (
    "# 数据集说明\n"
    "\n"
    "本数据集包含 1,000 条问答记录。\n"
    "## 数据规模\n"
    "train/dev/test 划分为 8:1:1。\n"
    "## 数据采集\n"
    "采集自公开政策文件，人工标注。\n"
).encode()

_METRIC_HEADER = re.compile(r"## 评测标准：(\d{4}) ")
_ITEM_HEADER = re.compile(r"### (\d{4}-\d{2}) ")
_NUMBERED = re.compile(r"^(\d+)\|(.*)$", re.MULTILINE)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        document_rubric_path=str(
            REPO_ROOT / "configs/document-evaluation/rubric/1.0.0.json"
        ),
        document_max_validation_retries=2,
    )


class ScriptedLLM:
    """按指标码注入模式的 mock LLM（默认输出 3 SATISFIED/5）。"""

    model = "mock-model"
    transport_retries = 1

    STATUSES: tuple[str, ...] = (
        "SATISFIED",
        "SATISFIED",
        "SATISFIED",
        "INSUFFICIENT",
        "MISSING",
    )

    def __init__(self, modes: dict[str, str] | None = None):
        self.modes = modes or {}
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.prompts.append(user)
        code = _METRIC_HEADER.search(user).group(1)
        mode = self.modes.get(code, "ok")
        if mode == "transport":
            raise LLMTransportError("connection reset")
        if mode == "invalid":
            return self._tampered(user)
        if mode == "invalid_then_ok" and self._calls_for(code) == 1:
            return self._tampered(user)
        return self._valid(user)

    def _calls_for(self, code: str) -> int:
        return sum(1 for p in self.prompts if f"## 评测标准：{code} " in p)

    def prompts_for(self, code: str) -> list[str]:
        return [p for p in self.prompts if f"## 评测标准：{code} " in p]

    @staticmethod
    def _tampered(user: str) -> dict:
        payload = ScriptedLLM._valid(user)
        for item in payload["items"]:
            item["item_code"] = "0000-99"
        return payload

    @staticmethod
    def _valid(user: str) -> dict:
        numbered = _NUMBERED.findall(user)
        first_line, first_text = numbered[0]
        items = []
        for code, status in zip(
            _ITEM_HEADER.findall(user), ScriptedLLM.STATUSES, strict=True
        ):
            if status == "MISSING":
                items.append(
                    {
                        "item_code": code,
                        "status": status,
                        "document_evidence": [],
                        "inspected_scope": "检查全文未发现相关内容",
                        "expected_section": "十、附录",
                        "reason": "文档缺少该内容",
                        "suggestion": "补充对应章节",
                        "fixability": "REQUIRES_HUMAN_INPUT",
                    }
                )
            else:
                items.append(
                    {
                        "item_code": code,
                        "status": status,
                        "document_evidence": [
                            {
                                "line_start": int(first_line),
                                "line_end": int(first_line),
                                "excerpt": first_text,
                            }
                        ],
                        "inspected_scope": "检查全文",
                        "reason": "内容达标",
                        "suggestion": "无需修改",
                        "expected_section": None,
                        "fixability": "AUTO_FIXABLE",
                    }
                )
        return {"items": items}


def _setup(db_session, tmp_path):
    dataset = DatasetModel(
        id=uuid4(), name=f"docs-{uuid4().hex[:8]}", modality=["text"]
    )
    db_session.add(dataset)
    db_session.flush()

    store = LocalFileSystemArtifactStore(tmp_path)
    document_service = DocumentVersionService(
        dataset_repository=SQLAlchemyDatasetRepository(db_session),
        document_version_repository=SQLAlchemyDocumentVersionRepository(db_session),
        artifact_store=store,
    )
    version = document_service.upload_document(dataset.id, "readme.md", CONTENT)
    return dataset, version, store


def _service(db_session, store, llm) -> DocumentReportTaskService:
    return DocumentReportTaskService(db_session, store, _settings(), llm_client=llm)


def test_success_flow_persists_report_and_artifacts(db_session, tmp_path):
    dataset, version, store = _setup(db_session, tmp_path)
    llm = ScriptedLLM()
    service = _service(db_session, store, llm)

    task = service.create_task(version.id)
    assert task["status"] == "PENDING"
    assert [m["metric_code"] for m in task["metrics"]] == [
        "0101",
        "0102",
        "0103",
        "0104",
    ]

    view = service.execute(UUID(task["task_id"]))
    assert view["status"] == "SUCCEEDED"
    assert view["error"] is None
    assert view["llm_calls"] == 4  # 每指标一次成功调用
    assert all(m["status"] == "OK" for m in view["metrics"])

    report = service.get_report(UUID(view["report_id"]))
    assert report is not None
    assert report.execution_status == "SUCCEEDED"

    # 总分恒等于程序复算：每指标 3/5 SATISFIED → 60 分
    scores = [
        o.value for s in report.metrics for o in s.observations if o.key == "score"
    ]
    assert scores == [60, 60, 60, 60]
    assert report.overall.score == round(sum(scores) / len(scores), 2)
    assert report.missing_items  # 每指标第 5 项 MISSING
    assert len(report.recommendations) == 8  # 每指标 2 项未满足

    # 解析产物落盘且版本元数据更新
    repository = SQLAlchemyDocumentVersionRepository(db_session)
    refreshed = repository.get_by_id(version.id)
    assert refreshed.parsed_artifact_uri is not None
    assert refreshed.parsed_artifact_uri.endswith("parsed/lines.json")
    assert store.exists(refreshed.parsed_artifact_uri)

    # 报告与逐指标 items 产物落盘
    base = f"datasets/{dataset.id}/documents/{version.id}/reports/{view['report_id']}"
    assert store.exists(f"{base}/report.json")
    for code in ("0101", "0102", "0103", "0104"):
        assert store.exists(f"{base}/metrics/{code}.items.json")


def test_partial_error_produces_partial_report_with_null_overall(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    llm = ScriptedLLM({"0104": "invalid"})
    service = _service(db_session, store, llm)

    task = service.create_task(version.id)
    view = service.execute(UUID(task["task_id"]))

    assert view["status"] == "PARTIAL_SUCCEEDED"
    by_code = {m["metric_code"]: m for m in view["metrics"]}
    assert by_code["0104"]["status"] == "ERROR"

    report = service.get_report(UUID(view["report_id"]))
    assert report.execution_status == "PARTIAL_SUCCEEDED"
    assert report.overall.score is None
    assert "0104" in report.overall.explanation
    error_section = next(s for s in report.metrics if s.metric.key == "0104")
    assert error_section.status == "ERROR"
    assert error_section.items == []


def test_all_error_metrics_fail_task_without_report(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    llm = ScriptedLLM({code: "invalid" for code in ("0101", "0102", "0103", "0104")})
    service = _service(db_session, store, llm)

    task = service.create_task(version.id)
    view = service.execute(UUID(task["task_id"]))

    assert view["status"] == "FAILED"
    assert view["report_id"] is None
    assert view["error"]["phase"] == "EVALUATION"

    count = (
        db_session.query(DocumentQualityReportModel)
        .filter_by(task_id=UUID(task["task_id"]))
        .count()
    )
    assert count == 0


def test_retry_exhaustion_consumes_configured_attempts(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    llm = ScriptedLLM({"0102": "invalid"})
    service = _service(db_session, store, llm)

    task = service.create_task(version.id)
    service.execute(UUID(task["task_id"]))

    # 1 + 2 次校验重试；第二次 prompt 必须带错误反馈
    prompts = llm.prompts_for("0102")
    assert len(prompts) == 3
    assert "未通过程序校验" in prompts[1]
    assert "0000-99" in prompts[1]


def test_transport_failure_fails_task_without_report_row(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    llm = ScriptedLLM({code: "transport" for code in ("0101", "0102", "0103", "0104")})
    service = _service(db_session, store, llm)

    task = service.create_task(version.id)
    view = service.execute(UUID(task["task_id"]))

    assert view["status"] == "FAILED"
    # 每指标一次调用即失败，不消耗校验重试
    assert len(llm.prompts) == 4
    count = (
        db_session.query(DocumentQualityReportModel)
        .filter_by(task_id=UUID(task["task_id"]))
        .count()
    )
    assert count == 0


def test_execute_rejects_missing_or_non_pending_task(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    service = _service(db_session, store, ScriptedLLM())

    import pytest

    with pytest.raises(ValueError, match="not found"):
        service.execute(uuid4())

    task = service.create_task(version.id)
    service.execute(UUID(task["task_id"]))
    with pytest.raises(ValueError, match="PENDING"):
        service.execute(UUID(task["task_id"]))


def test_sweep_marks_running_tasks_failed(db_session, tmp_path):
    _, version, store = _setup(db_session, tmp_path)
    service = _service(db_session, store, ScriptedLLM())

    orphan = DocumentReportTaskModel(
        id=uuid4(),
        document_version_id=version.id,
        status="RUNNING",
        evaluator_version="1.0.0",
        standard_version="1.0.0",
        standard_sha256="0" * 64,
        config={},
        document={
            "task_id": "pending",
            "status": "RUNNING",
            "metrics": [],
        },
    )
    db_session.add(orphan)
    db_session.flush()

    swept = service.sweep_interrupted()

    assert swept >= 1
    row = db_session.get(DocumentReportTaskModel, orphan.id)
    assert row.status == "FAILED"
    assert row.document["error"]["phase"] == "STARTUP_SWEEP"
    assert row.finished_at is not None

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档评测任务服务：任务创建（冻结配置快照）、执行编排
# （解析 → 逐指标 LLM 评测 → 报告汇总与落库）、查询与
# 孤儿 RUNNING 任务的启动清扫（D5）。
# ====================================================

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.document_evaluation.evaluator import (
    EVALUATOR_VERSION,
    DocumentEvaluator,
)
from qa_eval.application.document_evaluation.parser import (
    parse_markdown,
)
from qa_eval.application.document_evaluation.prompts import PROMPT_VERSION
from qa_eval.application.document_evaluation.report_builder import (
    build_report,
)
from qa_eval.application.document_evaluation.rubric import load_rubric
from qa_eval.config import Settings
from qa_eval.contracts.document import (
    DocumentEvaluatorInfo,
    DocumentQualityReportV1,
)
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.domain.repositories.document_version_repository import (
    DocumentVersionRepository,
)
from qa_eval.infrastructure.db.models.document_quality_report import (
    DocumentQualityReportModel,
)
from qa_eval.infrastructure.db.models.document_version import (
    DocumentReportTaskModel,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_document_version import (
    SQLAlchemyDocumentVersionRepository,
)
from qa_eval.infrastructure.uuid7 import uuid7

logger = logging.getLogger(__name__)

TASK_STATUSES = (
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL_SUCCEEDED",
    "FAILED",
)


class DocumentTaskTimeoutError(Exception):
    """文档评测任务超过整体时限，终止执行。"""


class DocumentReportTaskService:
    """文档评测任务生命周期；execute 由后台线程调用，逐阶段提交。"""

    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        settings: Settings,
        llm_client: Any | None = None,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.settings = settings
        self.llm_client = llm_client
        self._document_repository: DocumentVersionRepository = (
            SQLAlchemyDocumentVersionRepository(session)
        )

    # ------------------------------------------------
    # 创建
    # ------------------------------------------------
    def create_task(self, document_version_id: UUID) -> dict[str, Any]:
        """创建 PENDING 任务并冻结评测配置快照。

        - 文档版本不存在：LookupError（路由层映射 404）
        """

        version = self._document_repository.get_by_id(document_version_id)
        if version is None:
            raise LookupError(f"DocumentVersion not found: {document_version_id}")

        rubric, rubric_sha256 = load_rubric(self.settings.document_rubric_path)
        standard = rubric["standard"]

        task_id = uuid7()
        view: dict[str, Any] = {
            "task_id": str(task_id),
            "document_version_id": str(document_version_id),
            "dataset_id": str(version.dataset_id),
            "status": "PENDING",
            "error": None,
            "report_id": None,
            "metrics": [
                {
                    "metric_code": metric["metric_code"],
                    "metric_name": metric["metric_name"],
                    "status": "PENDING",
                }
                for metric in rubric["metrics"]
            ],
            "llm_calls": 0,
        }
        self.session.add(
            DocumentReportTaskModel(
                id=task_id,
                document_version_id=document_version_id,
                status="PENDING",
                evaluator_version=EVALUATOR_VERSION,
                standard_version=standard["version"],
                standard_sha256=rubric_sha256,
                config=self._config_snapshot(),
                document=view,
            )
        )
        self.session.flush()
        return dict(view)

    def _config_snapshot(self) -> dict[str, Any]:
        """发起时冻结 LLM 与重试参数，保证同任务内一致。"""

        return {
            "evaluator_version": EVALUATOR_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_output_tokens": self.settings.llm_max_output_tokens,
            "max_validation_retries": (self.settings.document_max_validation_retries),
        }

    # ------------------------------------------------
    # 执行
    # ------------------------------------------------
    def execute(self, task_id: UUID) -> dict[str, Any]:
        row = self.session.get(DocumentReportTaskModel, task_id)
        if row is None:
            raise ValueError(f"Document report task not found: {task_id}")
        view = dict(row.document)
        if view["status"] != "PENDING":
            raise ValueError("Only PENDING document report tasks can start")
        if self.llm_client is None:
            raise ValueError("LLM client is not configured")

        version = self._document_repository.get_by_id(UUID(view["document_version_id"]))
        if version is None:
            raise ValueError(
                "Document report task references a missing DocumentVersion"
            )

        view["status"] = "RUNNING"
        self._save(row, view, started=True)
        self.session.commit()

        deadline = datetime.now(UTC) + timedelta(
            seconds=self.settings.document_task_timeout_seconds
        )
        try:
            report_view = self._run_pipeline(row, view, version, deadline=deadline)
            return report_view
        except DocumentTaskTimeoutError as exc:
            self.session.rollback()
            row = self.session.get(DocumentReportTaskModel, task_id)
            view = dict(row.document)
            view["status"] = "FAILED"
            view["error"] = {"phase": "TIMEOUT", "message": str(exc)}
            self._save(row, view, finished=True)
            self.session.commit()
            raise
        except Exception as exc:
            self.session.rollback()
            row = self.session.get(DocumentReportTaskModel, task_id)
            view = dict(row.document)
            view["status"] = "FAILED"
            view["error"] = {
                "phase": "EXECUTION",
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            }
            self._save(row, view, finished=True)
            self.session.commit()
            raise

    def _run_pipeline(self, row, view: dict, version, *, deadline: datetime) -> dict[str, Any]:
        rubric, rubric_sha256 = load_rubric(self.settings.document_rubric_path)

        # 阶段 1：解析与行号化产物落盘
        content = self.artifact_store.read(version.storage_uri)
        parsed = parse_markdown(content)
        parsed_uri = self._write_json(
            f"datasets/{version.dataset_id}/documents/{version.id}/parsed/lines.json",
            parsed.to_artifact_document(),
        )
        if version.parsed_artifact_uri != parsed_uri:
            self._document_repository.set_parsed_artifact_uri(version.id, parsed_uri)
            self.session.commit()

        # 阶段 2：逐指标 LLM 评测（进度逐指标提交，重启可见）
        evaluator = DocumentEvaluator(
            self.llm_client,
            max_validation_retries=(self.settings.document_max_validation_retries),
            max_prompt_lines=self.settings.document_max_prompt_lines,
            max_prompt_chars=self.settings.document_max_prompt_chars,
        )

        def _on_metric(outcome) -> None:
            for metric in view["metrics"]:
                if metric["metric_code"] == outcome.metric_code:
                    metric["status"] = outcome.status
                    if outcome.status == "ERROR":
                        metric["error"] = outcome.error
                    break
            view["llm_calls"] = view.get("llm_calls", 0) + outcome.llm_calls
            self._save(row, view)
            self.session.commit()
            if datetime.now(UTC) >= deadline:
                raise DocumentTaskTimeoutError(
                    "文档评测任务执行超时，已终止；请重试或检查 LLM 网关配置"
                )

        result = evaluator.evaluate(rubric, parsed, on_metric=_on_metric)

        ok_count = sum(1 for outcome in result.outcomes if outcome.status == "OK")
        if ok_count == 0:
            # 全部指标 ERROR：任务失败且不产出报告（分数口径不可靠）
            view["status"] = "FAILED"
            view["error"] = {
                "phase": "EVALUATION",
                "message": "所有指标均为 ERROR，未生成质量报告",
            }
            self._save(row, view, finished=True)
            self.session.commit()
            return dict(view)

        # 阶段 3：报告汇总（分数程序复算）与产物落盘
        report_id = uuid7()
        report = build_report(
            report_id=report_id,
            task_id=UUID(view["task_id"]),
            document_version=version,
            line_count=parsed.line_count,
            rubric=rubric,
            rubric_sha256=rubric_sha256,
            result=result,
            evaluator=self._evaluator_info(),
            generated_at=datetime.now(UTC),
        )
        base = (
            f"datasets/{version.dataset_id}/documents/{version.id}/reports/{report_id}"
        )
        for section in report.metrics:
            if section.status != "OK":
                continue
            self._write_json(
                f"{base}/metrics/{section.metric.key}.items.json",
                {
                    "schema_version": "1.0",
                    "metric": section.metric.model_dump(mode="json"),
                    "items": [item.model_dump(mode="json") for item in section.items],
                },
            )
        self._write_json(f"{base}/report.json", report.model_dump(mode="json"))

        self.session.add(
            DocumentQualityReportModel(
                report_id=report_id,
                document_version_id=version.id,
                task_id=UUID(view["task_id"]),
                execution_status=report.execution_status,
                document=report.model_dump(mode="json"),
                generated_at=report.generated_at,
            )
        )
        # 无 relationship 时按类名排序 INSERT，先 flush 报告行再更新任务，
        # 避免 document_report_tasks 的 UPDATE 先行造成读写竞争歧义
        self.session.flush()

        view["status"] = report.execution_status
        view["report_id"] = str(report_id)
        self._save(row, view, finished=True)
        self.session.commit()
        return dict(view)

    def _evaluator_info(self) -> DocumentEvaluatorInfo:
        parameters = {
            "temperature": self.settings.llm_temperature,
            "max_output_tokens": self.settings.llm_max_output_tokens,
            "max_validation_retries": (self.settings.document_max_validation_retries),
            "transport_retries": getattr(self.llm_client, "transport_retries", None),
        }
        return DocumentEvaluatorInfo(
            version=EVALUATOR_VERSION,
            model=getattr(self.llm_client, "model", "unknown"),
            prompt_version=PROMPT_VERSION,
            parameters={
                key: value for key, value in parameters.items() if value is not None
            },
        )

    def _write_json(self, relative_path: str, payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        staging = self.artifact_store.prepare_write(relative_path)
        staging.write_bytes(encoded)
        return self.artifact_store.commit(relative_path).uri

    def _save(
        self,
        row: DocumentReportTaskModel,
        view: dict[str, Any],
        *,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        row.status = view["status"]
        row.document = dict(view)
        if started:
            row.started_at = datetime.now(UTC)
        if finished:
            row.finished_at = datetime.now(UTC)
        self.session.flush()

    # ------------------------------------------------
    # 查询与清扫
    # ------------------------------------------------
    def get_task(self, task_id: UUID) -> dict[str, Any] | None:
        row = self.session.get(DocumentReportTaskModel, task_id)
        if row is None:
            return None
        return self._task_view(row)

    def list_tasks(
        self, document_version_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(DocumentReportTaskModel).order_by(
            DocumentReportTaskModel.created_at.desc()
        )
        if document_version_id is not None:
            stmt = stmt.where(
                DocumentReportTaskModel.document_version_id == document_version_id
            )
        return [self._task_view(row) for row in self.session.scalars(stmt)]

    def get_report(self, report_id: UUID) -> DocumentQualityReportV1 | None:
        row = self.session.get(DocumentQualityReportModel, report_id)
        if row is None:
            return None
        return DocumentQualityReportV1.model_validate(row.document)

    def sweep_interrupted(self) -> int:
        """启动清扫：孤儿 RUNNING 任务置 FAILED（D5），返回清扫数量。"""

        stmt = select(DocumentReportTaskModel).where(
            DocumentReportTaskModel.status == "RUNNING"
        )
        swept = 0
        for row in self.session.scalars(stmt):
            view = dict(row.document)
            view["status"] = "FAILED"
            view["error"] = {
                "phase": "STARTUP_SWEEP",
                "message": "进程重启导致任务中断，请重新发起评测",
            }
            self._save(row, view, finished=True)
            swept += 1
        if swept:
            self.session.commit()
        return swept

    @staticmethod
    def _task_view(row: DocumentReportTaskModel) -> dict[str, Any]:
        view = dict(row.document)
        view["evaluator_version"] = row.evaluator_version
        view["standard_version"] = row.standard_version
        view["standard_sha256"] = row.standard_sha256
        view["created_at"] = row.created_at.isoformat() if row.created_at else None
        view["started_at"] = row.started_at.isoformat() if row.started_at else None
        view["finished_at"] = row.finished_at.isoformat() if row.finished_at else None
        return view

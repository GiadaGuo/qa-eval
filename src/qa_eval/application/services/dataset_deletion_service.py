from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.infrastructure.db.models.agent import AgentSessionModel
from qa_eval.infrastructure.db.models.artifact_cleanup import ArtifactCleanupTaskModel
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_manifest import DatasetManifestModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_quality_report import (
    DocumentQualityReportModel,
)
from qa_eval.infrastructure.db.models.document_version import (
    DocumentReportTaskModel,
    DocumentVersionModel,
)
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    LogicalEvaluationPlanModel,
    MetricResultModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    IssueIndexModel,
    QualityIssueModel,
    QualityReportModel,
    RuleEvaluationModel,
    ScoredQualityReportModel,
)

ACTIVE_RUN_STATUSES = ("PENDING", "RUNNING", "CANCEL_REQUESTED")
ACTIVE_DOCUMENT_TASK_STATUSES = ("PENDING", "RUNNING")
ACTIVE_AGENT_STATUSES = ("RUNNING",)


class DatasetDeletionService:
    """Analyze and transactionally remove the complete dataset ownership graph."""

    def __init__(self, session: Session):
        self.session = session

    def preview(self, dataset_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        dataset_stmt = select(DatasetModel).where(DatasetModel.id == dataset_id)
        if lock:
            dataset_stmt = dataset_stmt.with_for_update()
        dataset = self.session.scalar(dataset_stmt)
        if dataset is None:
            return None

        version_stmt = select(DatasetVersionModel).where(DatasetVersionModel.dataset_id == dataset_id)
        document_stmt = select(DocumentVersionModel).where(DocumentVersionModel.dataset_id == dataset_id)
        if lock:
            version_stmt = version_stmt.with_for_update()
            document_stmt = document_stmt.with_for_update()
        versions = list(self.session.scalars(version_stmt))
        documents = list(self.session.scalars(document_stmt))
        version_ids = [row.id for row in versions]
        document_ids = [row.id for row in documents]

        def count(model: type, column, ids: list[UUID]) -> int:
            if not ids:
                return 0
            return int(self.session.scalar(select(func.count()).select_from(model).where(column.in_(ids))) or 0)

        run_ids = list(self.session.scalars(select(EvaluationRunModel.run_id).where(EvaluationRunModel.dataset_version_id.in_(version_ids)))) if version_ids else []
        metric_ids = list(self.session.scalars(select(MetricResultModel.metric_result_id).where(MetricResultModel.run_id.in_(run_ids)))) if run_ids else []
        report_ids = list(self.session.scalars(select(QualityReportModel.report_id).where(QualityReportModel.run_id.in_(run_ids)))) if run_ids else []

        resources = {
            "dataset_versions": len(versions),
            "dataset_manifests": count(DatasetManifestModel, DatasetManifestModel.dataset_version_id, version_ids),
            "dataset_profiles": count(DatasetProfileModel, DatasetProfileModel.dataset_version_id, version_ids),
            "logical_evaluation_plans": count(LogicalEvaluationPlanModel, LogicalEvaluationPlanModel.dataset_version_id, version_ids),
            "physical_evaluation_plans": count(PhysicalEvaluationPlanModel, PhysicalEvaluationPlanModel.dataset_version_id, version_ids),
            "evaluation_runs": len(run_ids),
            "stage_runs": count(StageRunModel, StageRunModel.run_id, run_ids),
            "metric_results": len(metric_ids),
            "rule_evaluations": count(RuleEvaluationModel, RuleEvaluationModel.run_id, run_ids),
            "quality_issues": count(QualityIssueModel, QualityIssueModel.run_id, run_ids),
            "issue_indices": count(IssueIndexModel, IssueIndexModel.run_id, run_ids),
            "quality_reports": len(report_ids),
            "scored_quality_reports": count(ScoredQualityReportModel, ScoredQualityReportModel.run_id, run_ids),
            "quality_contract_versions": count(QualityContractVersionModel, QualityContractVersionModel.target_dataset_version_id, version_ids),
            "agent_sessions": count(AgentSessionModel, AgentSessionModel.dataset_version_id, version_ids),
            "document_versions": len(documents),
            "document_report_tasks": count(DocumentReportTaskModel, DocumentReportTaskModel.document_version_id, document_ids),
            "document_quality_reports": count(DocumentQualityReportModel, DocumentQualityReportModel.document_version_id, document_ids),
            "protected_versions": sum(1 for version_id in version_ids if self._version_is_referenced(version_id)),
        }
        blockers = self._blockers(version_ids, document_ids)
        return {
            "dataset": {"id": str(dataset.id), "name": dataset.name},
            "versions": [{"id": str(row.id), "version": row.version, "status": row.status} for row in versions],
            "resources": resources,
            "blockers": blockers,
            "can_cascade_delete": not blockers,
        }

    def cascade_delete(self, dataset_id: UUID) -> bool:
        preview = self.preview(dataset_id, lock=True)
        if preview is None:
            return False
        if preview["blockers"]:
            raise DatasetVersionInUseError(
                {item["resource"]: item["count"] for item in preview["blockers"]},
                code="DATASET_DELETE_BLOCKED",
                message="数据集存在正在执行的任务，请等待任务结束或取消后重试",
            )

        version_ids = [UUID(row["id"]) for row in preview["versions"]]
        document_rows = list(self.session.scalars(select(DocumentVersionModel).where(DocumentVersionModel.dataset_id == dataset_id)))
        document_ids = [row.id for row in document_rows]
        run_ids = list(self.session.scalars(select(EvaluationRunModel.run_id).where(EvaluationRunModel.dataset_version_id.in_(version_ids)))) if version_ids else []
        contract_ids = list(self.session.scalars(select(QualityContractVersionModel.contract_version_id).where(QualityContractVersionModel.target_dataset_version_id.in_(version_ids)))) if version_ids else []
        physical_ids = list(self.session.scalars(select(PhysicalEvaluationPlanModel.physical_plan_id).where(PhysicalEvaluationPlanModel.dataset_version_id.in_(version_ids)))) if version_ids else []
        logical_ids = list(self.session.scalars(select(LogicalEvaluationPlanModel.logical_plan_id).where(LogicalEvaluationPlanModel.dataset_version_id.in_(version_ids)))) if version_ids else []

        # Artifacts are queued in the same DB transaction; workers delete only after commit.
        # The dataset namespace is exclusively owned (versions + documents + reports).
        self._enqueue_artifact(f"datasets/{dataset_id}/", "PREFIX")
        for run_id in run_ids:
            self._enqueue_artifact(f"evaluation-runs/{run_id}/", "PREFIX")

        self._delete_in(DocumentQualityReportModel, DocumentQualityReportModel.document_version_id, document_ids)
        self._delete_in(DocumentReportTaskModel, DocumentReportTaskModel.document_version_id, document_ids)
        self._delete_in(DocumentVersionModel, DocumentVersionModel.id, document_ids)
        self._delete_in(ScoredQualityReportModel, ScoredQualityReportModel.run_id, run_ids)
        self._delete_in(QualityReportModel, QualityReportModel.run_id, run_ids)
        self._delete_in(RuleEvaluationModel, RuleEvaluationModel.run_id, run_ids)
        self._delete_in(QualityIssueModel, QualityIssueModel.run_id, run_ids)
        self._delete_in(IssueIndexModel, IssueIndexModel.run_id, run_ids)
        self._delete_in(MetricResultModel, MetricResultModel.run_id, run_ids)
        self._delete_in(StageRunModel, StageRunModel.run_id, run_ids)
        self._delete_in(EvaluationRunModel, EvaluationRunModel.run_id, run_ids)
        self._delete_in(PhysicalEvaluationPlanModel, PhysicalEvaluationPlanModel.physical_plan_id, physical_ids)
        self._delete_in(LogicalEvaluationPlanModel, LogicalEvaluationPlanModel.logical_plan_id, logical_ids)
        self._delete_in(QualityContractVersionModel, QualityContractVersionModel.contract_version_id, contract_ids)
        self._delete_in(AgentSessionModel, AgentSessionModel.dataset_version_id, version_ids)
        self._delete_in(DatasetManifestModel, DatasetManifestModel.dataset_version_id, version_ids)
        self._delete_in(DatasetProfileModel, DatasetProfileModel.dataset_version_id, version_ids)
        self._delete_in(DatasetVersionModel, DatasetVersionModel.id, version_ids)
        self.session.execute(delete(DatasetModel).where(DatasetModel.id == dataset_id))
        self.session.flush()
        return True

    def _version_is_referenced(self, version_id: UUID) -> bool:
        models = (
            (LogicalEvaluationPlanModel, LogicalEvaluationPlanModel.dataset_version_id),
            (PhysicalEvaluationPlanModel, PhysicalEvaluationPlanModel.dataset_version_id),
            (EvaluationRunModel, EvaluationRunModel.dataset_version_id),
            (MetricResultModel, MetricResultModel.dataset_version_id),
            (QualityReportModel, QualityReportModel.dataset_version_id),
            (QualityContractVersionModel, QualityContractVersionModel.target_dataset_version_id),
            (AgentSessionModel, AgentSessionModel.dataset_version_id),
        )
        return any(self.session.scalar(select(func.count()).select_from(model).where(column == version_id)) for model, column in models)

    def _blockers(self, version_ids: list[UUID], document_ids: list[UUID]) -> list[dict[str, Any]]:
        checks = []
        if version_ids:
            checks.extend([
                ("active_evaluation_runs", EvaluationRunModel, EvaluationRunModel.dataset_version_id.in_(version_ids), EvaluationRunModel.status.in_(ACTIVE_RUN_STATUSES), "存在未结束的评测运行"),
                ("active_agent_sessions", AgentSessionModel, AgentSessionModel.dataset_version_id.in_(version_ids), AgentSessionModel.status.in_(ACTIVE_AGENT_STATUSES), "存在正在执行的 Agent 会话"),
            ])
        if document_ids:
            checks.append(("active_document_report_tasks", DocumentReportTaskModel, DocumentReportTaskModel.document_version_id.in_(document_ids), DocumentReportTaskModel.status.in_(ACTIVE_DOCUMENT_TASK_STATUSES), "存在未结束的文档评测任务"))
        result = []
        for key, model, ownership, active, reason in checks:
            value = int(self.session.scalar(select(func.count()).select_from(model).where(ownership, active)) or 0)
            if value:
                result.append({"resource": key, "count": value, "reason": reason})
        return result

    def _enqueue_artifact(self, target: str, target_type: str) -> None:
        exists = self.session.scalar(select(ArtifactCleanupTaskModel.id).where(ArtifactCleanupTaskModel.target == target))
        if not exists:
            self.session.add(ArtifactCleanupTaskModel(target=target, target_type=target_type, status="PENDING", attempts=0))

    def _delete_in(self, model: type, column, ids: list[UUID]) -> None:
        if ids:
            self.session.execute(delete(model).where(column.in_(ids)))


def process_artifact_cleanup_tasks(session: Session, artifact_store, *, limit: int = 100) -> int:
    """Retry-safe outbox consumer. Missing objects count as successful deletion."""
    tasks = list(session.scalars(select(ArtifactCleanupTaskModel).where(ArtifactCleanupTaskModel.status == "PENDING").order_by(ArtifactCleanupTaskModel.created_at).limit(limit)))
    completed = 0
    for task in tasks:
        try:
            if task.target_type == "PREFIX":
                artifact_store.delete_prefix(task.target)
            else:
                artifact_store.delete(task.target)
            task.status = "COMPLETED"
            task.completed_at = datetime.now(UTC)
            task.last_error = None
            completed += 1
        except Exception as exc:  # noqa: BLE001 - provider errors must remain retryable
            task.last_error = str(exc)[:4000]
        finally:
            task.attempts += 1
    session.commit()
    return completed

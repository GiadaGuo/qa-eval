from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from qa_eval.application.evaluation.report_generator import (
    MetricCatalogEntry,
    build_quality_report,
)
from qa_eval.application.evaluation.rule_evaluator import evaluate_contract
from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.execution import EvaluationRunV1
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import MetricDefinitionV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    MetricResultModel,
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
)
from qa_eval.infrastructure.db.models.registry import MetricDefinitionModel
from qa_eval.infrastructure.uuid7 import uuid7


class QualityResultService:
    def __init__(self, session: Session, artifact_store: ArtifactStore):
        self.session = session
        self.artifact_store = artifact_store

    def finalize(self, run_id: UUID) -> QualityReportV1:
        run_row = self.session.get(EvaluationRunModel, run_id)
        if run_row is None:
            raise ValueError("EvaluationRun not found")
        if run_row.status not in {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED"}:
            raise ValueError("EvaluationRun must be terminal before report generation")
        existing = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        if existing is not None:
            return QualityReportV1.model_validate(existing.document)
        contract_row = self.session.get(
            QualityContractVersionModel, run_row.contract_version_id
        )
        if contract_row is None:
            raise ValueError("EvaluationRun contract is missing")
        contract = QualityContractVersionV1.model_validate(contract_row.document)
        result_rows = self.session.scalars(
            select(MetricResultModel).where(MetricResultModel.run_id == run_id)
        ).all()
        results = [MetricResultV1.model_validate(row.document) for row in result_rows]
        if not results:
            raise ValueError("EvaluationRun has no persisted MetricResults")
        now = datetime.now(UTC)
        evaluations = evaluate_contract(
            contract, results, id_factory=uuid7, evaluated_at=now
        )
        for evaluation in evaluations:
            self.session.add(
                RuleEvaluationModel(
                    rule_evaluation_id=evaluation.rule_evaluation_id,
                    run_id=evaluation.run_id,
                    metric_result_id=evaluation.metric_result_id,
                    status=evaluation.status,
                    document=evaluation.model_dump(mode="json"),
                )
            )
        self.session.flush()

        issues: list[QualityIssueV1] = []
        indices: dict[UUID, IssueIndexV1] = {}
        for result in results:
            result_issues = self._issues_for_result(result, now)
            issues.extend(result_issues)
            for issue in result_issues:
                self.session.add(
                    QualityIssueModel(
                        issue_id=issue.issue_id,
                        run_id=issue.run_id,
                        metric_result_id=issue.source.metric_result_id,
                        determination=issue.determination,
                        severity=issue.severity,
                        code=issue.code,
                        dedup_key=issue.dedup_key,
                        document=issue.model_dump(mode="json"),
                    )
                )
            self.session.flush()
            index = self._persist_issue_index(result, result_issues)
            indices[result.metric_result_id] = index
            self.session.add(
                IssueIndexModel(
                    issue_index_id=index.issue_index_id,
                    run_id=index.run_id,
                    metric_result_id=result.metric_result_id,
                    document=index.model_dump(mode="json"),
                )
            )
            self.session.flush()

        catalog = [self._catalog(result) for result in results]
        report = build_quality_report(
            contract,
            results,
            evaluations,
            issues,
            indices,
            catalog,
            execution_status=run_row.status,
            id_factory=uuid7,
            generated_at=now,
        )
        self.session.add(
            QualityReportModel(
                report_id=report.report_id,
                run_id=report.run_id,
                dataset_version_id=report.dataset_version_id,
                contract_version_id=report.contract_version_id,
                execution_status=report.execution_status,
                gate_status=report.gate_status,
                document=report.model_dump(mode="json"),
                generated_at=report.generated_at,
            )
        )
        self.session.flush()
        self._restore_run_after_late_finalization(run_row)
        return report

    def _restore_run_after_late_finalization(self, run_row: EvaluationRunModel) -> None:
        """Successful (re-)finalization: clear a recorded finalization failure and
        restore the execution status implied by the persisted StageRuns."""
        run = EvaluationRunV1.model_validate(run_row.document)
        if run.error is None or run.error.get("phase") != "QUALITY_RESULT_FINALIZATION":
            return
        stage_statuses = self.session.scalars(
            select(StageRunModel.status).where(StageRunModel.run_id == run_row.run_id)
        ).all()
        failures = sum(1 for status in stage_statuses if status == "FAILED")
        status = (
            "FAILED"
            if stage_statuses and failures == len(stage_statuses)
            else "PARTIAL_SUCCEEDED"
            if failures
            else "SUCCEEDED"
        )
        restored = run.model_copy(update={"status": status, "error": None})
        run_row.status = status
        run_row.document = restored.model_dump(mode="json")

    def _issues_for_result(
        self, result: MetricResultV1, created_at: datetime
    ) -> list[QualityIssueV1]:
        if result.status != "OK":
            return []
        matches = [item for item in result.evidence if item.kind == "DUPLICATE_GROUPS"]
        if len(matches) != 1:
            raise ValueError(
                "MetricResult must reference one DUPLICATE_GROUPS evidence"
            )
        evidence = matches[0]
        content = self.artifact_store.read(evidence.artifact.uri)
        if hashlib.sha256(content).hexdigest() != evidence.artifact.checksum.value:
            raise ValueError("DUPLICATE_GROUPS evidence checksum mismatch")
        groups = json.loads(content)
        issues: list[QualityIssueV1] = []
        for group in groups["groups"]:
            representative = group["representative_sample_id"]
            for sample_id in group["sample_ids"]:
                if sample_id == representative:
                    continue
                issues.append(
                    self._issue(
                        result,
                        created_at,
                        evidence.evidence_id,
                        measurement="exact_redundant_record_ratio",
                        detector="exact_duplicate_redundant_sample",
                        code="EXACT_DUPLICATE_REDUNDANT",
                        determination="CONFIRMED",
                        severity="WARNING",
                        sample_id=sample_id,
                        message="该样本与重复组代表样本的规范化正文完全相同。",
                        locator={
                            "group_id": group["group_id"],
                            "normalized_sha256": group["normalized_sha256"],
                        },
                        related=[
                            {
                                "relation": "DUPLICATE_OF",
                                "subject": {
                                    "type": "SAMPLE",
                                    "sample_id": representative,
                                },
                            }
                        ],
                    )
                )
        for group in groups["near_duplicate_groups"]:
            representative = group["sample_ids"][0]
            for sample_id in group["sample_ids"]:
                related = []
                if sample_id != representative:
                    related.append(
                        {
                            "relation": "NEAR_DUPLICATE_GROUP_REPRESENTATIVE",
                            "subject": {
                                "type": "SAMPLE",
                                "sample_id": representative,
                            },
                        }
                    )
                issues.append(
                    self._issue(
                        result,
                        created_at,
                        evidence.evidence_id,
                        measurement="near_duplicate_candidate_record_count",
                        detector="near_duplicate_candidate_sample",
                        code="NEAR_DUPLICATE_CANDIDATE",
                        determination="CANDIDATE",
                        severity="INFO",
                        sample_id=sample_id,
                        message="该样本由 MinHash + LSH 召回为近重复候选，尚需复核。",
                        locator={"group_id": group["group_id"]},
                        related=related,
                    )
                )
        return issues

    @staticmethod
    def _issue(
        result,
        created_at,
        evidence_id,
        *,
        measurement,
        detector,
        code,
        determination,
        severity,
        sample_id,
        message,
        locator,
        related,
    ):
        return QualityIssueV1.model_validate(
            {
                "issue_id": uuid7(),
                "run_id": result.run_id,
                "dataset_version_id": result.dataset_version_id,
                "source": {
                    "metric_result_id": result.metric_result_id,
                    "metric_key": result.metric.key,
                    "metric_version": result.metric.version,
                    "measurement": measurement,
                    "rule_evaluation_id": None,
                },
                "detector": {"key": detector, "version": "1.0.0"},
                "category": "DUPLICATE_CONTENT",
                "code": code,
                "determination": determination,
                "severity": severity,
                "scope": "SAMPLE",
                "subject": {"type": "SAMPLE", "sample_id": sample_id},
                "related_subjects": related,
                "message": message,
                "evidence": [{"evidence_id": evidence_id, "locator": locator}],
                "dedup_key": f"{result.metric.key}:{code}:{locator['group_id']}:{sample_id}",
                "status": "OPEN",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )

    def _persist_issue_index(self, result, issues):
        payload = "".join(
            json.dumps(
                issue.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
            + "\n"
            for issue in issues
        ).encode()
        relative = (
            f"evaluation-runs/{result.run_id}/issues/{result.metric_result_id}.jsonl"
        )
        staging = self.artifact_store.prepare_write(relative)
        staging.write_bytes(payload)
        stored = self.artifact_store.commit(relative)
        counts = Counter(issue.determination for issue in issues)
        artifact = ArtifactRef.model_validate(
            {
                "artifact_id": uuid7(),
                "kind": "ISSUE_INDEX",
                "uri": stored.uri,
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/quality-issue/1.0.schema.json",
                "size_bytes": stored.size_bytes,
                "record_count": len(issues),
                "checksum": {"algorithm": "sha256", "value": stored.sha256},
            }
        )
        return IssueIndexV1.model_validate(
            {
                "issue_index_id": uuid7(),
                "run_id": result.run_id,
                "dataset_version_id": result.dataset_version_id,
                "artifact": artifact.model_dump(mode="json"),
                "supported_filters": [
                    "metric_key",
                    "code",
                    "determination",
                    "severity",
                    "scope",
                    "sample_id",
                ],
                "summary": {
                    "confirmed_count": counts["CONFIRMED"],
                    "candidate_count": counts["CANDIDATE"],
                },
            }
        )

    def _catalog(self, result):
        row = self.session.scalar(
            select(MetricDefinitionModel).filter_by(
                metric_key=result.metric.key, version=result.metric.version
            )
        )
        if row is None:
            raise ValueError("Metric catalog metadata is missing")
        metric = MetricDefinitionV1.model_validate(row.document)
        return MetricCatalogEntry(
            key=metric.metric_key,
            version=metric.version,
            name=metric.name,
            dimension=metric.dimension,
        )

    def has_run(self, run_id: UUID) -> bool:
        return self.session.get(EvaluationRunModel, run_id) is not None

    def list_reports(self) -> list[QualityReportV1]:
        rows = self.session.scalars(
            select(QualityReportModel).order_by(QualityReportModel.generated_at.desc())
        ).all()
        return [QualityReportV1.model_validate(row.document) for row in rows]

    def get_report(self, run_id: UUID) -> QualityReportV1 | None:
        row = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        return None if row is None else QualityReportV1.model_validate(row.document)

    def list_rules(self, run_id: UUID) -> list[RuleEvaluationV1]:
        rows = self.session.scalars(
            select(RuleEvaluationModel).where(RuleEvaluationModel.run_id == run_id)
        ).all()
        return [RuleEvaluationV1.model_validate(row.document) for row in rows]

    def list_issues(
        self,
        run_id: UUID,
        *,
        determination: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[QualityIssueV1]]:
        conditions = [QualityIssueModel.run_id == run_id]
        if determination:
            conditions.append(QualityIssueModel.determination == determination)
        total = self.session.scalar(
            select(func.count()).select_from(QualityIssueModel).where(*conditions)
        )
        rows = self.session.scalars(
            select(QualityIssueModel)
            .where(*conditions)
            .order_by(QualityIssueModel.issue_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return total or 0, [QualityIssueV1.model_validate(row.document) for row in rows]

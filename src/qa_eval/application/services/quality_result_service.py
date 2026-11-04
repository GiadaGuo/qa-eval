from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from qa_eval.application.evaluation.operational_scoring import (
    DEFAULT_OPERATIONAL_PROFILE_ID,
    build_operational_scoring_report,
    load_scoring_profile,
)
from qa_eval.application.evaluation.report_generator import (
    MetricCatalogEntry,
    build_quality_report,
)
from qa_eval.application.evaluation.rule_evaluator import evaluate_contract
from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.datasets import SampleRecordV2
from qa_eval.contracts.execution import EvaluationRunV1
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    QualityReportV1_1,
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import MetricDefinitionV1
from qa_eval.contracts.scoring import OperationalScoringReportV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
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
    ScoredQualityReportModel,
)
from qa_eval.infrastructure.db.models.registry import MetricDefinitionModel
from qa_eval.infrastructure.uuid7 import uuid7


class QualityResultService:
    def __init__(self, session: Session, artifact_store: ArtifactStore):
        self.session = session
        self.artifact_store = artifact_store

    def finalize(self, run_id: UUID) -> QualityReportV1 | QualityReportV1_1:
        run_row = self.session.get(EvaluationRunModel, run_id)
        if run_row is None:
            raise ValueError("EvaluationRun not found")
        if run_row.status not in {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED"}:
            raise ValueError("EvaluationRun must be terminal before report generation")
        existing = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        if existing is not None:
            return self._parse_report(existing.document)
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
            result_evaluations = [
                item for item in evaluations if item.metric_result_id == result.metric_result_id
            ]
            result_issues = self._issues_for_result(
                result,
                result_evaluations,
                {item.key: item.severity for item in contract.items},
                now,
            )
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

        catalog = self._catalog(contract)
        stage_rows = self.session.scalars(
            select(StageRunModel).where(StageRunModel.run_id == run_id)
        ).all()
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
            stage_runs={row.stage_run_id: row.document for row in stage_rows},
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
        self,
        result: MetricResultV1,
        evaluations: list[RuleEvaluationV1],
        severity_by_item: dict[str, str],
        created_at: datetime,
    ) -> list[QualityIssueV1]:
        if result.status not in {"OK", "PARTIAL"}:
            return []
        issues = self._rule_failure_issues(
            result, evaluations, severity_by_item, created_at
        )
        # Issue detectors are metric-specific. Metrics without a registered
        # detector still receive an empty IssueIndex and a complete report.
        if result.metric.key != "02080103":
            return issues
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
        # The low-cost exact-only implementation intentionally emits no
        # MinHash candidate groups.
        for group in groups.get("near_duplicate_groups", []):
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

    def _rule_failure_issues(
        self,
        result: MetricResultV1,
        evaluations: list[RuleEvaluationV1],
        severity_by_item: dict[str, str],
        created_at: datetime,
    ) -> list[QualityIssueV1]:
        """Materialize only evidence-backed threshold failures.

        OBSERVE_ONLY evaluations are SKIPPED and therefore can never create a
        synthetic rule-failure issue here.
        """
        if not result.evidence:
            return []
        evidence = result.evidence[0]
        issues = []
        for evaluation in evaluations:
            if evaluation.status != "FAIL":
                continue
            issues.append(
                QualityIssueV1.model_validate(
                    {
                        "issue_id": uuid7(),
                        "run_id": result.run_id,
                        "dataset_version_id": result.dataset_version_id,
                        "source": {
                            "metric_result_id": result.metric_result_id,
                            "metric_key": result.metric.key,
                            "metric_version": result.metric.version,
                            "measurement": evaluation.metric.measurement,
                            "rule_evaluation_id": evaluation.rule_evaluation_id,
                        },
                        "detector": {"key": "threshold-rule-failure", "version": "1.0.0"},
                        "category": "RULE_VIOLATION",
                        "code": "THRESHOLD_VIOLATED",
                        "determination": "CONFIRMED",
                        "severity": severity_by_item[evaluation.contract_item_key],
                        "scope": "DATASET",
                        "subject": {"type": "DATASET", "dataset_version_id": result.dataset_version_id},
                        "message": "该指标的已观测值违反发布契约中的阈值规则。",
                        "evidence": [{"evidence_id": evidence.evidence_id, "locator": {"measurement": evaluation.metric.measurement}}],
                        "dedup_key": f"{result.metric.key}:THRESHOLD_VIOLATED:{evaluation.rule_evaluation_id}",
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
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

    def _catalog(self, contract: QualityContractVersionV1) -> list[MetricCatalogEntry]:
        rows = self.session.scalars(select(MetricDefinitionModel)).all()
        definitions = [MetricDefinitionV1.model_validate(row.document) for row in rows]
        selected = {
            (item.metric.key, item.metric.version): item.metric.measurement
            for item in contract.items
        }
        known = {(item.metric_key, item.version) for item in definitions}
        missing = set(selected) - known
        if missing:
            raise ValueError(f"Metric catalog metadata is missing: {sorted(missing)}")
        catalog = []
        for metric in definitions:
            measurement_key = selected.get((metric.metric_key, metric.version))
            measurement = next(
                (item for item in metric.measurements if item.key == measurement_key),
                None,
            )
            catalog.append(
                MetricCatalogEntry(
                    key=metric.metric_key,
                    version=metric.version,
                    name=metric.name,
                    dimension=metric.dimension,
                    parent_metric_key=metric.parent_metric_key,
                    measurement_key=measurement_key,
                    measurement_direction=None if measurement is None else measurement.direction,
                    measurement_minimum=None if measurement is None else measurement.minimum,
                    measurement_maximum=None if measurement is None else measurement.maximum,
                    guide_status=(
                        "APPLICABLE"
                        if (metric.metric_key, metric.version) in selected
                        else "NOT_EVALUATED"
                    ),
                    guide_reason=(
                        "Metric was selected by the published contract"
                        if (metric.metric_key, metric.version) in selected
                        else "Metric was not selected for this evaluation run"
                    ),
                )
            )
        return catalog

    @staticmethod
    def _parse_report(document: dict) -> QualityReportV1 | QualityReportV1_1:
        if document.get("schema_version") == "1.1":
            return QualityReportV1_1.model_validate(document)
        return QualityReportV1.model_validate(document)

    def has_run(self, run_id: UUID) -> bool:
        return self.session.get(EvaluationRunModel, run_id) is not None

    def list_reports(self) -> list[QualityReportV1 | QualityReportV1_1]:
        rows = self.session.scalars(
            select(QualityReportModel).order_by(QualityReportModel.generated_at.desc())
        ).all()
        return [self._parse_report(row.document) for row in rows]

    def get_report(self, run_id: UUID) -> QualityReportV1 | QualityReportV1_1 | None:
        row = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        return None if row is None else self._parse_report(row.document)

    def rebuild_operational_report(
        self,
        run_id: UUID,
        *,
        profile_id: str = DEFAULT_OPERATIONAL_PROFILE_ID,
    ) -> OperationalScoringReportV1:
        """Build an immutable scoring snapshot from persisted facts only.

        This never executes a Calculator and never rewrites MetricResult,
        QualityContract, RuleEvaluation, Gate status, or the base report.
        """

        profile = load_scoring_profile(profile_id)
        existing = self.session.scalar(
            select(ScoredQualityReportModel).where(
                ScoredQualityReportModel.run_id == run_id,
                ScoredQualityReportModel.scoring_profile_id == profile.profile_id,
                ScoredQualityReportModel.scoring_profile_version == profile.profile_version,
                ScoredQualityReportModel.report_schema_version == "1.2-mvp",
            )
        )
        if existing is not None:
            return OperationalScoringReportV1.model_validate(existing.document)
        base_row = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        if base_row is None:
            raise ValueError("QualityReport not found; finalize the terminal Run first")
        base = self._parse_report(base_row.document)
        if not isinstance(base, QualityReportV1_1):
            raise ValueError(  # noqa: TRY004 - invalid persisted report version
                "Operational scoring requires a QualityReport 1.1 base snapshot"
            )
        result_rows = self.session.scalars(
            select(MetricResultModel).where(MetricResultModel.run_id == run_id)
        ).all()
        stage_rows = self.session.scalars(
            select(StageRunModel).where(StageRunModel.run_id == run_id)
        ).all()
        report = build_operational_scoring_report(
            base,
            [MetricResultV1.model_validate(row.document) for row in result_rows],
            {row.stage_run_id: row.document for row in stage_rows},
            profile,
        )
        self.session.add(
            ScoredQualityReportModel(
                scored_report_id=report.scored_report_id,
                base_report_id=report.base_report_id,
                run_id=report.run_id,
                scoring_profile_id=report.scoring_profile_id,
                scoring_profile_version=report.scoring_profile_version,
                report_schema_version=report.report_schema_version,
                document=report.model_dump(mode="json"),
                generated_at=report.generated_at,
            )
        )
        self.session.flush()
        return report

    def get_operational_report(
        self,
        run_id: UUID,
        *,
        profile_id: str = DEFAULT_OPERATIONAL_PROFILE_ID,
    ) -> OperationalScoringReportV1 | None:
        row = self.session.scalar(
            select(ScoredQualityReportModel)
            .where(
                ScoredQualityReportModel.run_id == run_id,
                ScoredQualityReportModel.scoring_profile_id == profile_id,
            )
            .order_by(ScoredQualityReportModel.generated_at.desc())
        )
        return None if row is None else OperationalScoringReportV1.model_validate(row.document)

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
        metric_result_id: UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[QualityIssueV1]]:
        conditions = [QualityIssueModel.run_id == run_id]
        if determination:
            conditions.append(QualityIssueModel.determination == determination)
        if metric_result_id is not None:
            conditions.append(QualityIssueModel.metric_result_id == metric_result_id)
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

    def get_sample_by_id(
        self, dataset_version_id: UUID, sample_id: str
    ) -> SampleRecordV2 | None:
        """Read a single SampleRecord from the normalized artifact by sample_id."""
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None:
            raise ValueError("DatasetVersion not found")
        refs = version.artifact_refs or {}
        normalized_ref = refs.get("normalized")
        if normalized_ref is None:
            raise ValueError("DatasetVersion has no normalized artifact")
        content = self.artifact_store.read(normalized_ref["uri"])
        for line in content.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("sample_id") == sample_id:
                return SampleRecordV2.model_validate(record)
        return None

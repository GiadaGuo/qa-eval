from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from qa_eval.application.evaluation.unified_report import QualityReportV2
from qa_eval.application.evaluation.operational_scoring import (
    DEFAULT_OPERATIONAL_PROFILE_ID,
    build_operational_scoring_report,
    load_scoring_profile,
    with_field_relation_v2,
)
from qa_eval.application.evaluation.report_generator import (
    MetricCatalogEntry,
    build_quality_report,
)
from qa_eval.application.evaluation.rule_evaluator import (
    evaluate_contract,
    evaluate_native_item,
)
from qa_eval.application.issues.protocol import IssueBuildContext
from qa_eval.application.issues.registry import IssueBuilderRegistry
from qa_eval.application.normalization.record_reader import RawSampleRecord
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.calculator_results import CalculatorResultV1
from qa_eval.contracts.datasets import SampleRecordV2
from qa_eval.contracts.execution import EvaluationRunV1
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    IssueSource,
    NativeEvaluationReportSection,
    NativeRuleEvaluationV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    QualityReportV1_1,
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import MetricDefinitionV1
from qa_eval.contracts.scoring import OperationalScoringReportV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.evaluation_run import (
    CalculatorResultModel,
    EvaluationRunModel,
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
from qa_eval.infrastructure.db.models.registry import MetricDefinitionModel
from qa_eval.infrastructure.uuid7 import uuid7


class QualityResultService:
    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        issue_builders: IssueBuilderRegistry | None = None,
    ):
        self.session = session
        self.artifact_store = artifact_store
        self.issue_builders = issue_builders or IssueBuilderRegistry()

    def finalize(self, run_id: UUID) -> QualityReportV1 | QualityReportV1_1 | QualityReportV2:
        run_row = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if run_row is None:
            raise ValueError("EvaluationRun not found")
        if run_row.status not in {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED"}:
            raise ValueError("EvaluationRun must be terminal before report generation")
        existing = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id, QualityReportModel.source_report_id.is_(None))
        )
        if existing is not None:
            return self._parse_report(existing.document)
        if run_row.document_version_id:
            from qa_eval.application.services.unified_finalization import UnifiedFinalization

            self._restore_run_after_late_finalization(run_row)
            return UnifiedFinalization(self.session).finalize_document(run_id, self.artifact_store)
        if self._unified_pending(run_id):
            raise ValueError("unified finalization has not published")
        contract_row = self.session.get(
            QualityContractVersionModel, run_row.contract_version_id
        )
        if contract_row is None:
            raise ValueError("EvaluationRun contract is missing")
        contract = QualityContractVersionV1.model_validate(contract_row.document)
        result_rows = self.session.scalars(
            select(MetricResultModel).where(
                MetricResultModel.run_id == run_id,
                MetricResultModel.schema_version == "1.0",
            )
        ).all()
        results = [MetricResultV1.model_validate(row.document) for row in result_rows]
        if not results and not any(
            item.strategy == "EVALUATE" for item in contract.observation_items
        ):
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

        issues_by_result: dict[UUID, list[QualityIssueV1]] = {}
        # Build and validate every candidate set before making any Issue visible
        # in the database. A late Builder failure therefore cannot leave a
        # partially materialized Issue collection in this transaction.
        for result in results:
            result_evaluations = [
                item
                for item in evaluations
                if item.metric_result_id == result.metric_result_id
            ]
            issues_by_result[result.metric_result_id] = self._issues_for_result(
                result,
                result_evaluations,
                {item.key: item.severity for item in contract.items},
                now,
            )

        issues: list[QualityIssueV1] = []
        indices: dict[UUID, IssueIndexV1] = {}
        for result in results:
            result_issues = self._upsert_issues(
                issues_by_result[result.metric_result_id]
            )
            issues.extend(result_issues)
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
        native_sections, native_issues = self._finalize_native(
            run_row, contract, stage_rows, now
        )
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
            native_sections=native_sections,
            policy_format_result=self._policy_format_result(native_sections),
            native_issues=native_issues,
            run_identity=(run_row.run_id, run_row.dataset_version_id),
        )
        self.session.add(
            QualityReportModel(
                schema_version=report.schema_version,
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

    def _finalize_native(self, run_row, contract, stage_rows, now):
        items = [
            item for item in contract.observation_items if item.strategy == "EVALUATE"
        ]
        if not items:
            return [], []
        run = EvaluationRunV1.model_validate(run_row.document)
        plan = self.session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        slot_stages = {}
        for stage in plan.document["stages"]:
            for binding in stage.get("output_bindings", []):
                if binding.get("output_kind") == "CALCULATOR_RESULT":
                    slot = binding["output_slot_id"]
                    if slot in slot_stages:
                        raise ValueError(
                            "native output slot is assigned to multiple stages"
                        )
                    slot_stages[slot] = stage["stage_id"]
        latest = {}
        for row in stage_rows:
            if row.stage_id not in latest or row.attempt > latest[row.stage_id].attempt:
                latest[row.stage_id] = row
        results = {
            (row.stage_run_id, row.output_slot_id): CalculatorResultV1.model_validate(
                row.document
            )
            for row in self.session.scalars(
                select(CalculatorResultModel).where(
                    CalculatorResultModel.run_id == run.run_id
                )
            )
        }
        sections, issues = [], []
        for item in items:
            slot = item.statistic_ref.output_slot_id
            stage = latest.get(slot_stages[slot])
            result = results.get((stage.stage_run_id, slot)) if stage else None
            if (
                result is not None
                and result.plan_fingerprint != plan.semantic_fingerprint
            ):
                raise ValueError(
                    "native rule result belongs to a different plan fingerprint"
                )
            evaluation = evaluate_native_item(
                contract, item, run, result, id_factory=uuid7, evaluated_at=now
            )
            self.session.add(
                RuleEvaluationModel(
                    rule_evaluation_id=evaluation.rule_evaluation_id,
                    run_id=run.run_id,
                    calculator_result_id=evaluation.calculator_result_id,
                    status=evaluation.status,
                    document=evaluation.model_dump(mode="json"),
                )
            )
            built = (
                []
                if result is None
                else self.issue_builders.build_native(
                    result, evaluation, now, self.artifact_store
                )
            )
            persisted = self._upsert_issues(built)
            issues.extend(persisted)
            sections.append(
                NativeEvaluationReportSection(
                    evaluation=evaluation,
                    affected_record_count=len(
                        {issue.subject.sample_id for issue in persisted}
                    ),
                    issue_count=len(persisted),
                    evidence_count=len(
                        {
                            (ref.evidence_id, ref.locator["row_id"])
                            for issue in persisted
                            for ref in issue.evidence
                        }
                    ),
                )
            )
        return sections, issues

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
        context = IssueBuildContext(
            result=result,
            evaluations=tuple(evaluations),
            severity_by_item=severity_by_item,
            created_at=created_at,
            artifact_store=self.artifact_store,
        )
        issues = self.issue_builders.build(context)
        if not (issues and self.issue_builders.replaces_threshold_summary(context)):
            issues.extend(
                self._rule_failure_issues(
                    result, evaluations, severity_by_item, created_at
                )
            )
        return sorted(issues, key=lambda item: item.dedup_key)

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
                        "detector": {
                            "key": "threshold-rule-failure",
                            "version": "1.0.0",
                        },
                        "category": "RULE_VIOLATION",
                        "code": "THRESHOLD_VIOLATED",
                        "determination": "CONFIRMED",
                        "severity": severity_by_item[evaluation.contract_item_key],
                        "scope": "DATASET",
                        "subject": {
                            "type": "DATASET",
                            "dataset_version_id": result.dataset_version_id,
                        },
                        "message": "该指标的已观测值违反发布契约中的阈值规则。",
                        "evidence": [
                            {
                                "evidence_id": evidence.evidence_id,
                                "locator": {
                                    "measurement": evaluation.metric.measurement
                                },
                            }
                        ],
                        "dedup_key": (
                            f"{result.metric.key}:{result.metric.version}:"
                            f"THRESHOLD_VIOLATED:{evaluation.contract_item_key}:"
                            f"{evaluation.metric.measurement}"
                        ),
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
            )
        return issues

    def _upsert_issues(self, candidates: list[QualityIssueV1]) -> list[QualityIssueV1]:
        """Idempotently persist candidates while preserving managed state."""
        persisted: list[QualityIssueV1] = []
        for candidate in candidates:
            row = self.session.scalar(
                select(QualityIssueModel).where(
                    QualityIssueModel.run_id == candidate.run_id,
                    QualityIssueModel.dedup_key == candidate.dedup_key,
                )
            )
            if row is None:
                self.session.add(
                    QualityIssueModel(
                        issue_id=candidate.issue_id,
                        run_id=candidate.run_id,
                        metric_result_id=candidate.source.metric_result_id
                        if isinstance(candidate.source, IssueSource)
                        else None,
                        calculator_result_id=getattr(
                            candidate.source, "calculator_result_id", None
                        ),
                        determination=candidate.determination,
                        severity=candidate.severity,
                        code=candidate.code,
                        dedup_key=candidate.dedup_key,
                        document=candidate.model_dump(mode="json"),
                    )
                )
                persisted.append(candidate)
                continue

            existing = QualityIssueV1.model_validate(row.document)
            # issue_id, lifecycle status and creation time are state owned by
            # the persisted Issue, not by a repeatable detector run.
            merged = candidate.model_copy(
                update={
                    "issue_id": existing.issue_id,
                    "status": existing.status,
                    "created_at": existing.created_at,
                    "updated_at": existing.updated_at,
                    "determination": existing.determination
                    if existing.status != "OPEN"
                    else candidate.determination,
                }
            )
            row.metric_result_id = getattr(merged.source, "metric_result_id", None)
            row.calculator_result_id = getattr(
                merged.source, "calculator_result_id", None
            )
            row.determination = merged.determination
            row.severity = merged.severity
            row.code = merged.code
            row.document = merged.model_dump(mode="json")
            persisted.append(merged)
        return persisted

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

    def _policy_format_result(self, sections):
        result_ids = {
            section.evaluation.calculator_result_id
            for section in sections
            if section.evaluation.metric.key == "0201"
            and section.evaluation.calculator.key == "policy_document_format"
            and section.evaluation.calculator_result_id is not None
        }
        if len(result_ids) != 1:
            return None
        row = self.session.get(CalculatorResultModel, next(iter(result_ids)))
        return CalculatorResultV1.model_validate(row.document) if row else None

    def _catalog(self, contract: QualityContractVersionV1) -> list[MetricCatalogEntry]:
        rows = self.session.scalars(select(MetricDefinitionModel)).all()
        definitions = [MetricDefinitionV1.model_validate(row.document) for row in rows]
        selected = {
            (item.metric.key, item.metric.version): item.metric.measurement
            for item in contract.items
        }
        selected.update(
            {
                (item.metric.key, item.metric.version): item.statistic_ref.statistics_key
                for item in contract.observation_items
                if item.strategy == "EVALUATE"
                and (item.metric.key, item.metric.version) == ("0207", "2.0.0")
            }
        )
        for item in contract.observation_items:
            if item.strategy == "EVALUATE" and item.metric.key == "0201":
                selected.setdefault((item.metric.key, item.metric.version), "compliant_record_ratio")
        selected_0207 = next(
            (version for key, version in selected if key == "0207"), None
        )
        known = {(item.metric_key, item.version) for item in definitions}
        missing = set(selected) - known
        if missing:
            raise ValueError(f"Metric catalog metadata is missing: {sorted(missing)}")
        catalog = []
        for metric in definitions:
            if metric.metric_key == "0207" and selected_0207 and metric.version != selected_0207:
                continue
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
                    measurement_direction=None
                    if measurement is None
                    else measurement.direction,
                    measurement_minimum=None
                    if measurement is None
                    else measurement.minimum,
                    measurement_maximum=None
                    if measurement is None
                    else measurement.maximum,
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
    def _parse_report(document: dict) -> QualityReportV1 | QualityReportV1_1 | QualityReportV2:
        if document.get("schema_version") == "2.0":
            return QualityReportV2.model_validate(document)
        if document.get("schema_version") == "1.1":
            return QualityReportV1_1.model_validate(document)
        return QualityReportV1.model_validate(document)

    def has_run(self, run_id: UUID) -> bool:
        return self.session.get(EvaluationRunModel, run_id) is not None

    def list_reports(self) -> list[QualityReportV1 | QualityReportV1_1 | QualityReportV2]:
        rows = self.session.scalars(
            select(QualityReportModel).order_by(QualityReportModel.generated_at.desc())
        ).all()
        return [self._parse_report(row.document) for row in rows]

    def get_report(self, run_id: UUID) -> QualityReportV1 | QualityReportV1_1 | QualityReportV2 | None:
        row = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id, QualityReportModel.source_report_id.is_(None))
        )
        return None if row is None else self._parse_report(row.document)

    def combine_report(self, run_id, document_report_id, dataset_version_id):
        from qa_eval.application.services.unified_finalization import UnifiedFinalization

        return UnifiedFinalization(self.session).combine_snapshots(
            run_id, document_report_id, dataset_version_id
        )

    def get_report_snapshot(self, report_id):
        row = self.session.get(QualityReportModel, report_id)
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
        base_row = self.session.scalar(
            select(QualityReportModel).where(QualityReportModel.run_id == run_id, QualityReportModel.source_report_id.is_(None))
        )
        if base_row is None:
            raise ValueError("QualityReport not found; finalize the terminal Run first")
        base = self._parse_report(base_row.document)
        if not isinstance(base, QualityReportV1_1):
            raise ValueError(  # noqa: TRY004 - invalid persisted report version
                "Operational scoring requires a QualityReport 1.1 base snapshot"
            )
        field_relation_evaluations = [
            section.evaluation
            for section in base.native_evaluations
            if (section.evaluation.metric.key, section.evaluation.metric.version)
            == ("0207", "2.0.0")
        ]
        primary = [
            rule
            for rule in field_relation_evaluations
            if rule.statistic_ref.statistics_key == "confirmed_consistent_record_ratio"
        ]
        candidates = primary or field_relation_evaluations
        field_relation_result = None
        if len(candidates) == 1:
            result_id = candidates[0].calculator_result_id
            row = (
                self.session.get(CalculatorResultModel, result_id)
                if result_id
                else None
            )
            if row is not None:
                field_relation_result = CalculatorResultV1.model_validate(row.document)
        if candidates and not (
            field_relation_result
            and field_relation_result.status_detail.get("pilot_only")
        ):
            profile = with_field_relation_v2(profile)
        existing = self.session.scalar(
            select(ScoredQualityReportModel).where(
                ScoredQualityReportModel.run_id == run_id,
                ScoredQualityReportModel.scoring_profile_id == profile.profile_id,
                ScoredQualityReportModel.scoring_profile_version
                == profile.profile_version,
                ScoredQualityReportModel.report_schema_version == "1.2-mvp",
            )
        )
        if existing is not None:
            return OperationalScoringReportV1.model_validate(existing.document)
        result_rows = self.session.scalars(
            select(MetricResultModel).where(
                MetricResultModel.run_id == run_id,
                MetricResultModel.schema_version == "1.0",
            )
        ).all()
        stage_rows = self.session.scalars(
            select(StageRunModel).where(StageRunModel.run_id == run_id)
        ).all()
        report = build_operational_scoring_report(
            base,
            [MetricResultV1.model_validate(row.document) for row in result_rows],
            {row.stage_run_id: row.document for row in stage_rows},
            profile,
            field_relation_result=field_relation_result,
            policy_format_result=self._policy_format_result(base.native_evaluations),
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
        return (
            None
            if row is None
            else OperationalScoringReportV1.model_validate(row.document)
        )

    def _unified_pending(self, run_id: UUID) -> bool:
        row = self.session.get(EvaluationRunModel, run_id, populate_existing=True)
        checkpoint = row.document.get("execution", {}).get("unified_finalization") if row else None
        return bool(checkpoint and checkpoint["step"] < 3)

    def list_rules(
        self, run_id: UUID
    ) -> list[RuleEvaluationV1 | NativeRuleEvaluationV1]:
        if self._unified_pending(run_id):
            return []
        rows = self.session.scalars(
            select(RuleEvaluationModel).where(RuleEvaluationModel.run_id == run_id)
        ).all()
        return [
            (
                NativeRuleEvaluationV1
                if row.document["schema_version"] == "1.1"
                else RuleEvaluationV1
            ).model_validate(row.document)
            for row in rows
        ]

    def list_issues(
        self,
        run_id: UUID,
        *,
        determination: str | None = None,
        metric_result_id: UUID | None = None,
        calculator_result_id: UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[QualityIssueV1]]:
        if self._unified_pending(run_id):
            return 0, []
        conditions = [QualityIssueModel.run_id == run_id]
        if determination:
            conditions.append(QualityIssueModel.determination == determination)
        if metric_result_id is not None:
            conditions.append(QualityIssueModel.metric_result_id == metric_result_id)
        if calculator_result_id is not None:
            conditions.append(
                QualityIssueModel.calculator_result_id == calculator_result_id
            )
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
    ) -> SampleRecordV2 | RawSampleRecord | None:
        """Read a single record using its published representation."""
        return EvidenceQueryService(self.session, self.artifact_store).get_sample_by_id(
            dataset_version_id, sample_id
        )

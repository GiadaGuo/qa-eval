"""Resumable V2 finalization using the Run execution document."""

import json
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from qa_eval.application.evaluation.scoring import (
    ComponentScore,
    score_document_aspects,
    weighted_required_scores,
)
from qa_eval.application.evaluation.unified_report import (
    DocumentMapping,
    QualityReportV2,
    ReportDimension,
    ReportSnapshotRef,
    coverage_gate,
    dimension,
    document_profile,
    report_dimensions,
)
from qa_eval.application.services.logical_planner_service import semantic_fingerprint
from qa_eval.application.services.metric_aggregation_service import (
    MetricAggregationService,
)
from qa_eval.application.services.scoring_consumer import ScoringConsumer, ScoringInputs
from qa_eval.contracts.calculator_results import CalculatorResultV1
from qa_eval.contracts.document import DocumentQualityReportV1
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_quality_report import (
    DocumentQualityReportModel,
)
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    CalculatorResultModel,
    EvaluationRunModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    QualityIssueModel,
    QualityReportModel,
)


class FinalizationBlocked(ValueError):
    pass


class FinalizationBackend(Protocol):
    """Trusted upstream integration. Operations are retry-safe and own their transactions."""

    def verify_artifacts(self, inputs: ScoringInputs) -> None: ...
    def materialize_rules_and_issues(self, inputs: ScoringInputs) -> None: ...


class UnifiedFinalization:
    def __init__(self, session: Session, backend: FinalizationBackend | None = None):
        self.session = session
        self.backend = backend

    @classmethod
    def for_store(cls, session, store):
        from qa_eval.application.services.finalization_backend import (
            NativeFinalizationBackend,
        )

        return cls(
            session, NativeFinalizationBackend(sessionmaker(session.get_bind()), store)
        )

    def finalize_run(
        self,
        consumer: ScoringConsumer,
        run_id: UUID,
        *,
        selected_attempts: dict[str, UUID] | None = None,
        mode: str = "BASIC",
        document: DocumentMapping | None = None,
    ) -> QualityReportV2:
        if consumer.session is self.session:
            raise ValueError("consumer and finalizer require separate sessions")
        if mode != "BASIC":
            raise FinalizationBlocked("FULL_DISABLED")
        inputs = consumer.read(run_id, selected_attempts=selected_attempts)
        if "selected_attempts" not in inputs.run.execution:
            raise FinalizationBlocked("PERSISTED_ATTEMPT_SELECTION_UNAVAILABLE")
        if document is not None:
            raise FinalizationBlocked("PERSISTED_DOCUMENT_SCORING_MAPPING_UNAVAILABLE")
        return self.finalize(inputs, mode=mode)

    def finalize(
        self,
        inputs: ScoringInputs,
        *,
        mode: str = "BASIC",
        document: DocumentMapping | None = None,
    ) -> QualityReportV2:
        """Own transactions on a dedicated session. Verified work must be idempotent."""
        if mode != "BASIC":
            raise FinalizationBlocked("FULL_DISABLED")
        if not inputs.formal_ready or self.backend is None:
            raise FinalizationBlocked(
                "FROZEN_PRODUCTION_INPUTS_OR_FINALIZATION_BACKEND_UNAVAILABLE"
            )
        run_id = inputs.run.run_id
        selection = {
            str(part.stage_id): str(part.stage_run_id)
            for part in inputs.components
            if part.stage_run_id is not None
        }
        results = sorted(
            str(part.result.calculator_result_id)
            for part in inputs.components
            if part.result is not None
        )
        frozen = {
            "attempts": selection,
            "results": results,
            "mode": mode,
            "plan": inputs.plan.semantic_fingerprint,
            "contract": inputs.contract.fingerprint,
            "input": inputs.run.input_fingerprint,
            "document": document.model_dump(mode="json") if document else None,
        }
        frozen["scoring"] = semantic_fingerprint(
            {
                "nodes": [node.model_dump(mode="json") for node in inputs.nodes],
                "slots": {
                    part.key.output_slot_id: {
                        "config": part.configuration.model_dump(mode="json")
                        if part.configuration
                        else None,
                        "result": part.result.model_dump(mode="json")
                        if part.result
                        else None,
                    }
                    for part in inputs.components
                },
            }
        )
        identity = semantic_fingerprint(frozen)
        with self.session.begin():
            row = self._locked_run(inputs)
            current = self._checkpoint(row)
            if current and current["identity"] != identity:
                raise ValueError("finalization selection is already frozen")
            existing = self._published(run_id)
            if existing:
                return existing
            if not current:
                current = {"identity": identity, "frozen": frozen, "step": 0}
                self._store_checkpoint(row, current)
            step = current["step"]

        for target, work in (
            (1, self.backend.verify_artifacts),
            (2, self.backend.materialize_rules_and_issues),
        ):
            if step >= target:
                continue
            work(inputs)
            with self.session.begin():
                row = self._locked_run(inputs)
                checkpoint = self._checkpoint(row)
                if checkpoint["identity"] != identity:
                    raise ValueError("finalization identity changed")
                if checkpoint["step"] < target:
                    self._store_checkpoint(row, {**checkpoint, "step": target})

        with self.session.begin():
            document_report = self._read_document(inputs, document)
            counts = self._issue_counts(inputs)
        dimensions = report_dimensions(inputs, document_report, document)
        gates = {
            part.key.output_slot_id: coverage_gate(
                part, inputs.coverage_policy(part.key)
            )
            for part in inputs.components
        }
        coverage_reasons = tuple(
            f"COVERAGE_{gate.verdict}:{slot}"
            for slot, gate in sorted(gates.items())
            if gate.verdict in {"FAIL", "INDETERMINATE"}
        )
        if coverage_reasons:
            documentation, data = dimensions
            verdict = data.verdict
            if any(gate.verdict == "INDETERMINATE" for gate in gates.values()):
                verdict = "INDETERMINATE"
            elif verdict != "INDETERMINATE":
                verdict = "FAIL"
            dimensions = (
                documentation,
                data.model_copy(
                    update={
                        "verdict": verdict,
                        "reasons": (*data.reasons, *coverage_reasons),
                    }
                ),
            )
        statuses = {dimension.verdict for dimension in dimensions}
        overall = (
            "INDETERMINATE"
            if "INDETERMINATE" in statuses
            else ("FAIL" if "FAIL" in statuses else "PASS")
        )
        with self.session.begin():
            row = self._locked_run(inputs)
            checkpoint = self._checkpoint(row)
            if checkpoint["identity"] != identity or checkpoint["step"] < 2:
                raise ValueError("finalization checkpoints incomplete")
            existing = self._published(run_id)
            if existing:
                return existing
            MetricAggregationService(self.session).persist(inputs)
            payload = QualityReportV2(
                report_id=uuid5(run_id, "quality-report/2.0"),
                run_id=run_id,
                dataset_version_id=inputs.run.dataset_version_id,
                contract_version_id=inputs.contract.contract_version_id,
                contract_fingerprint=frozen["contract"],
                plan_fingerprint=frozen["plan"],
                input_fingerprint=frozen["input"],
                fingerprint="",
                finalized_at=datetime.now(UTC).isoformat(),
                dimensions=dimensions,
                overall_verdict=overall,
                gate_reasons=tuple(
                    dict.fromkeys(
                        reason
                        for dimension in dimensions
                        for reason in dimension.reasons
                    )
                ),
                issue_counts=counts,
                coverage_gates=gates,
                document_reference=document if document_report else None,
            )
            payload.fingerprint = semantic_fingerprint(
                payload.model_dump(mode="json", exclude={"fingerprint"})
            )
            self.session.add(
                QualityReportModel(
                    report_id=payload.report_id,
                    run_id=run_id,
                    dataset_version_id=payload.dataset_version_id,
                    contract_version_id=payload.contract_version_id,
                    execution_status=row.status,
                    gate_status=payload.overall_verdict,
                    schema_version="2.0",
                    document_quality_report_id=document_report.report_id
                    if document_report
                    else None,
                    document=payload.model_dump(mode="json"),
                )
            )
            self._store_checkpoint(row, {**checkpoint, "step": 3})
            return payload

    def finalize_document(self, run_id, store):
        """Publish the document Run's immutable V2 snapshot inside the caller transaction."""
        run = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if (
            run is None
            or run.document_version_id is None
            or run.status
            not in {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED", "CANCELED"}
        ):
            raise FinalizationBlocked("terminal document Run required")
        existing = self._published(run_id)
        if existing:
            return existing
        contract = QualityContractVersionV1.model_validate(
            self.session.get(
                QualityContractVersionModel, run.contract_version_id
            ).document
        )
        plan = self.session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        target = contract.document_target
        if target is None or target.document_version_id != run.document_version_id:
            raise ValueError("document contract and Run targets differ")
        results = self.session.scalars(
            select(CalculatorResultModel).where(CalculatorResultModel.run_id == run_id)
        ).all()
        stages = {
            stage.stage_run_id: stage
            for stage in self.session.scalars(
                select(StageRunModel).where(StageRunModel.run_id == run_id)
            )
        }
        latest_stages = {}
        for stage in stages.values():
            prior = latest_stages.get(stage.stage_id)
            if prior is None or prior.attempt < stage.attempt:
                latest_stages[stage.stage_id] = stage
        selected = run.document.get("execution", {}).get("selected_attempts")
        if selected is not None:
            latest_stages = {}
            for key, identity in selected.items():
                attempt = stages.get(UUID(identity))
                if attempt is None or attempt.stage_id != key:
                    raise ValueError("invalid frozen document attempt selection")
                latest_stages[key] = attempt
        latest = {}
        for row in results:
            stage = latest_stages.get(row.contract_item_key)
            if (
                stage
                and stage.stage_run_id == row.stage_run_id
                and stage.status in {"SUCCEEDED", "FAILED"}
            ):
                if row.contract_item_key in latest:
                    raise ValueError("ambiguous document result for selected attempt")
                latest[row.contract_item_key] = row
        profile = document_profile(target.weights)
        parts, facts = [], {}
        issue_count = 0
        for metric in profile.metrics:
            key = metric.metric_id
            row = latest.get(key)
            result = CalculatorResultV1.model_validate(row.document) if row else None
            stats = result.statistics if result else {}
            score = score_document_aspects(
                result.status if result else "MISSING", stats
            )
            checks = []
            if result:
                expected = next(item for item in contract.items if item.key == key)
                config = expected.calculation.implementation_constraints.attributes
                if (
                    result.document_version_id != run.document_version_id
                    or result.plan_fingerprint != plan.semantic_fingerprint
                    or result.input_fingerprint != run.document["input_fingerprint"]
                    or result.metric.key != key
                    or result.calculator_key != config["calculator_key"]
                    or result.calculator_version != config["calculator_version"]
                ):
                    raise ValueError(
                        "document result does not match frozen execution identity"
                    )
                for evidence in result.evidence_artifact_refs:
                    content = store.read(evidence.artifact.uri)
                    if sha256(content).hexdigest() != evidence.artifact.checksum.value:
                        raise ValueError("document evidence checksum mismatch")
                    checks.extend(
                        {**json.loads(line), "evidence_id": str(evidence.evidence_id)}
                        for line in content.splitlines()
                    )
                if result.status in {"OK", "NOT_APPLICABLE"}:
                    configured = expected.calculation.parameters["configuration"][
                        "items"
                    ]
                    codes = [item["item_code"] for item in configured]
                    if (
                        [check["item_code"] for check in checks] != codes
                        or result.status_detail.get("configuration_sha256")
                        != config["configuration_sha256"]
                        or any(
                            check["document_sha256"] != target.sha256
                            or check["configuration_sha256"]
                            != config["configuration_sha256"]
                            for check in checks
                        )
                    ):
                        raise ValueError(
                            "document evidence differs from frozen configuration"
                        )
                    evaluated = [
                        check
                        for check in checks
                        if check["outcome"] != "NOT_APPLICABLE"
                    ]
                    if result.status == "OK" and (
                        len(evaluated) != stats["required_aspect_count"]
                        or sum(check["outcome"] == "SATISFIED" for check in evaluated)
                        != stats["satisfied_aspect_count"]
                    ):
                        raise ValueError("document evidence and counters differ")
                    if result.status == "NOT_APPLICABLE" and evaluated:
                        raise ValueError(
                            "non-applicable result contains applicable checks"
                        )
            weight = Fraction(str(metric.weight))
            parts.append(
                ComponentScore(
                    key,
                    "CALCULATOR_RESULT",
                    result.calculator_result_id if result else None,
                    score,
                    base_weight=weight,
                    effective_weight=weight,
                )
            )
            facts[key] = {
                "satisfied_aspect_count": stats.get("satisfied_aspect_count"),
                "required_aspect_count": stats.get("required_aspect_count"),
                "checks": tuple(checks),
                "stage_run_id": row.stage_run_id if row else None,
                "evidence_paths": tuple(
                    f"/api/v1/evidence/{ref.evidence_id}/rows"
                    for ref in result.evidence_artifact_refs
                )
                if result
                else (),
            }
            issue_count += sum(
                check["outcome"] in {"INSUFFICIENT", "MISSING"} for check in checks
            )
        scored = dimension(
            "DOCUMENTATION",
            weighted_required_scores(tuple(parts)),
            threshold=target.threshold,
        )
        scored = scored.model_copy(
            update={
                "metrics": tuple(
                    metric.model_copy(update=facts[metric.key])
                    for metric in scored.metrics
                )
            }
        )
        payload = QualityReportV2(
            report_id=uuid5(run_id, "quality-report/2.0"),
            run_id=run_id,
            document_version_id=run.document_version_id,
            contract_version_id=run.contract_version_id,
            contract_fingerprint=contract.fingerprint,
            plan_fingerprint=plan.semantic_fingerprint,
            input_fingerprint=run.document["input_fingerprint"],
            fingerprint="",
            finalized_at=datetime.now(UTC).isoformat(),
            mode="DOCUMENT",
            dimensions=(scored,),
            overall_verdict=scored.verdict,
            gate_reasons=scored.reasons,
            issue_counts={"CONFIRMED": issue_count},
            scoring_profile=profile.model_dump(mode="json"),
        )
        return self._save_snapshot(payload, run.status)

    def combine_snapshots(self, dataset_run_id, document_report_id, dataset_version_id):
        run = self.session.get(EvaluationRunModel, dataset_run_id, with_for_update=True)
        if run is None or run.dataset_version_id != dataset_version_id:
            raise ValueError("selected Run and confirmed DatasetVersion differ")
        data_row = self.session.scalar(
            select(QualityReportModel).where(
                QualityReportModel.run_id == dataset_run_id,
                QualityReportModel.source_report_id.is_(None),
            )
        )
        document_row = self.session.get(QualityReportModel, document_report_id)
        if (
            data_row is None
            or document_row is None
            or document_row.document_version_id is None
        ):
            raise FinalizationBlocked(
                "published dataset and document snapshots required"
            )
        document = QualityReportV2.model_validate(document_row.document)
        dataset_version = self.session.get(DatasetVersionModel, dataset_version_id)
        document_version = self.session.get(
            DocumentVersionModel, document.document_version_id
        )
        if (
            document.mode != "DOCUMENT"
            or dataset_version.dataset_id != document_version.dataset_id
        ):
            raise ValueError("selected DocumentVersion must belong to the same Dataset")
        document_run = self.session.get(EvaluationRunModel, document.run_id)
        if document_run.status != "SUCCEEDED":
            raise ValueError("selected document Run must have completed successfully")
        identity = uuid5(data_row.report_id, str(document_report_id))
        existing = self.session.get(QualityReportModel, identity)
        if existing:
            return QualityReportV2.model_validate(existing.document)
        published = data_row.document
        if published.get("schema_version") == "2.0":
            base = QualityReportV2.model_validate(published)
            data = next(
                (item for item in base.dimensions if item.key == "DATA_QUALITY"), None
            )
            gates = base.coverage_gates
            dataset_issues = base.issue_counts
            plan_fingerprint, contract_fingerprint = (
                base.plan_fingerprint,
                base.contract_fingerprint,
            )
        else:
            data, gates, dataset_issues = None, {}, {}
            plan_fingerprint = self.session.get(
                PhysicalEvaluationPlanModel, run.physical_plan_id
            ).semantic_fingerprint
            contract_fingerprint = self.session.get(
                QualityContractVersionModel, run.contract_version_id
            ).fingerprint
        if data is None:
            data = ReportDimension(
                key="DATA_QUALITY",
                score=None,
                status="MISSING",
                verdict="INDETERMINATE",
                metrics=(),
                reasons=("FORMAL_DATA_QUALITY_SCORE_UNAVAILABLE",),
            )
        dimensions = (document.dimensions[0], data)
        verdicts = {item.verdict for item in dimensions}
        overall = (
            "INDETERMINATE"
            if "INDETERMINATE" in verdicts
            else ("FAIL" if "FAIL" in verdicts else "PASS")
        )
        payload = QualityReportV2(
            report_id=identity,
            run_id=run.run_id,
            dataset_version_id=dataset_version_id,
            contract_version_id=run.contract_version_id,
            contract_fingerprint=contract_fingerprint,
            plan_fingerprint=plan_fingerprint,
            input_fingerprint=run.document["input_fingerprint"],
            fingerprint="",
            finalized_at=datetime.now(UTC).isoformat(),
            dimensions=dimensions,
            overall_verdict=overall,
            gate_reasons=tuple(
                dict.fromkeys(reason for item in dimensions for reason in item.reasons)
            ),
            issue_counts={
                key: document.issue_counts.get(key, 0) + dataset_issues.get(key, 0)
                for key in document.issue_counts.keys() | dataset_issues.keys()
            },
            coverage_gates=gates,
            document_snapshot=ReportSnapshotRef(
                report_id=document.report_id,
                run_id=document.run_id,
                version_id=document.document_version_id,
                contract_version_id=document.contract_version_id,
                fingerprint=document.fingerprint,
            ),
            dataset_snapshot=ReportSnapshotRef(
                report_id=data_row.report_id,
                run_id=data_row.run_id,
                version_id=dataset_version_id,
                contract_version_id=data_row.contract_version_id,
                fingerprint=published.get("fingerprint")
                or semantic_fingerprint(published),
            ),
        )
        return self._save_snapshot(payload, run.status)

    def _save_snapshot(self, payload, execution_status):
        payload.fingerprint = semantic_fingerprint(
            payload.model_dump(mode="json", exclude={"fingerprint"})
        )
        self.session.add(
            QualityReportModel(
                report_id=payload.report_id,
                run_id=payload.run_id,
                dataset_version_id=payload.dataset_version_id,
                document_version_id=payload.document_version_id,
                source_report_id=payload.dataset_snapshot.report_id
                if payload.dataset_snapshot
                else None,
                source_document_report_id=payload.document_snapshot.report_id
                if payload.document_snapshot
                else None,
                contract_version_id=payload.contract_version_id,
                execution_status=execution_status,
                gate_status=payload.overall_verdict,
                schema_version="2.0",
                document=payload.model_dump(mode="json"),
            )
        )
        self.session.flush()
        return payload

    def _locked_run(self, inputs):
        row = self.session.get(
            EvaluationRunModel,
            inputs.run.run_id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            row is None
            or row.status not in {"SUCCEEDED", "PARTIAL_SUCCEEDED"}
            or (
                row.physical_plan_id,
                row.contract_version_id,
                row.dataset_version_id,
                row.document.get("input_fingerprint"),
            )
            != (
                inputs.plan.physical_plan_id,
                inputs.contract.contract_version_id,
                inputs.run.dataset_version_id,
                inputs.run.input_fingerprint,
            )
        ):
            raise ValueError("Run is not eligible for frozen finalization")
        return row

    @staticmethod
    def _checkpoint(row):
        return row.document.get("execution", {}).get("unified_finalization")

    @staticmethod
    def _store_checkpoint(row, value):
        row.document = {
            **row.document,
            "execution": {
                **row.document.get("execution", {}),
                "unified_finalization": value,
            },
        }

    def _published(self, run_id):
        row = self.session.scalar(
            select(QualityReportModel).where(
                QualityReportModel.run_id == run_id,
                QualityReportModel.source_report_id.is_(None),
            )
        )
        if row is None:
            return None
        if row.schema_version != "2.0":
            raise FinalizationBlocked("HISTORICAL_REPORT_ALREADY_FINALIZED")
        return QualityReportV2.model_validate(row.document)

    def _read_document(self, inputs, mapping):
        if mapping is None:
            return None
        row = self.session.get(DocumentQualityReportModel, mapping.report_id)
        if row is None:
            return None
        report = DocumentQualityReportV1.model_validate(row.document)
        version = self.session.get(DocumentVersionModel, row.document_version_id)
        dataset = self.session.get(DatasetVersionModel, inputs.run.dataset_version_id)
        if (
            version is None
            or dataset is None
            or (version.dataset_id, dataset.dataset_id, report.dataset_id)
            != (mapping.dataset_id,) * 3
            or (
                report.report_id,
                report.document_version_id,
                report.task_id,
                report.execution_status,
                report.evaluation_standard.version,
                report.evaluation_standard.sha256,
                report.evaluator.version,
                semantic_fingerprint(row.document),
            )
            != (
                mapping.report_id,
                mapping.document_version_id,
                row.task_id,
                row.execution_status,
                mapping.rubric_version,
                mapping.rubric_sha256,
                mapping.evaluator_version,
                mapping.report_fingerprint,
            )
            or row.document_version_id != mapping.document_version_id
            or report.document.sha256 != version.sha256
            or report.document.version != version.version
        ):
            raise ValueError("document report does not match its frozen reference")
        return report

    def _issue_counts(self, inputs):
        ids = [
            part.result.calculator_result_id
            for part in inputs.components
            if part.result is not None
        ]
        query = (
            select(QualityIssueModel.determination, func.count())
            .where(
                QualityIssueModel.run_id == inputs.run.run_id,
                QualityIssueModel.calculator_result_id.in_(ids),
            )
            .group_by(QualityIssueModel.determination)
        )
        counts = dict(self.session.execute(query).all())
        return {
            key: counts.get(key, 0) for key in ("CONFIRMED", "CANDIDATE", "SUSPECTED")
        }

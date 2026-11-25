"""Versioned run-level metric results. The caller owns the transaction."""

from dataclasses import asdict
from fractions import Fraction
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.evaluation.scoring import (
    SCORER_VERSION,
    MetricAggregator,
    MetricScore,
)
from qa_eval.application.services.logical_planner_service import semantic_fingerprint
from qa_eval.application.services.scoring_consumer import (
    ComponentKey,
    ScoringInputs,
    Status,
)
from qa_eval.contracts.base import WireModel
from qa_eval.contracts.metrics import MetricResultV1, VersionedKey
from qa_eval.infrastructure.db.models.evaluation_run import (
    CalculatorResultModel,
    EvaluationRunModel,
    MetricResultModel,
    StageRunModel,
)


class MetricComponentV2(WireModel):
    kind: Literal["CALCULATOR_RESULT", "METRIC_RESULT"]
    ref: UUID | None
    identity: dict[str, str | None]
    status: Status
    score: str | None
    blocking: bool
    reasons: tuple[str, ...]
    base_weight: str
    effective_weight: str
    mapping: tuple[str, str] | None
    statistics: tuple[str, ...]
    basis: tuple[int, int] | None
    coverage_basis: tuple[int, int] | None


class MetricResultV2(WireModel):
    schema_version: Literal["2.0"] = "2.0"
    metric_result_id: UUID
    run_id: UUID
    dataset_version_id: UUID
    metric: VersionedKey
    status: Status
    score: str | None
    blocking: bool
    reasons: tuple[str, ...]
    components: tuple[MetricComponentV2, ...]
    scorer_version: str
    input_fingerprint: str
    plan_fingerprint: str
    contract_fingerprint: str
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def _exact(value: Fraction | None) -> str | None:
    return str(value) if value is not None else None


class MetricAggregationService:
    def __init__(self, session: Session):
        self.session = session

    def read(self, result_id: UUID) -> MetricResultV1 | MetricResultV2 | None:
        row = self.session.get(MetricResultModel, result_id)
        if row is None:
            return None
        if row.schema_version == "1.0":
            return MetricResultV1.model_validate(row.document)
        result = MetricResultV2.model_validate(row.document)
        body = result.model_dump(mode="json", exclude={"fingerprint"})
        if semantic_fingerprint(body) != result.fingerprint or (
            result.metric_result_id,
            result.run_id,
            result.dataset_version_id,
            result.metric.key,
            result.metric.version,
            result.status,
        ) != (
            row.metric_result_id,
            row.run_id,
            row.dataset_version_id,
            row.metric_key,
            row.metric_version,
            row.status,
        ):
            raise ValueError("metric result fingerprint or ownership mismatch")
        return result

    def persist(self, inputs: ScoringInputs) -> tuple[MetricResultV2, ...]:
        if not inputs.formal_ready or not inputs.nodes or not inputs.components:
            raise ValueError("formal scoring inputs are unavailable")
        return self._persist(inputs)

    def _persist(self, inputs: ScoringInputs) -> tuple[MetricResultV2, ...]:
        aggregation = MetricAggregator().aggregate(inputs)
        if any(metric.aggregate.score.blocking for metric in aggregation.metrics):
            raise ValueError("metric aggregation has blocking components")
        documents = tuple(
            self._document(inputs, metric) for metric in aggregation.metrics
        )
        run = self.session.get(
            EvaluationRunModel, inputs.run.run_id, with_for_update=True
        )
        if run is None or (
            run.physical_plan_id,
            run.contract_version_id,
            run.dataset_version_id,
            run.document.get("input_fingerprint"),
        ) != (
            inputs.plan.physical_plan_id,
            inputs.contract.contract_version_id,
            inputs.run.dataset_version_id,
            inputs.run.input_fingerprint,
        ):
            raise ValueError("persisted Run does not match frozen scoring inputs")
        stages = {stage.stage_id: stage for stage in inputs.plan.stages}
        for component in inputs.components:
            result = component.result
            if result is None:
                continue
            if component.source != "NATIVE":
                raise ValueError(
                    "legacy results cannot be persisted as formal aggregates"
                )
            stage = stages.get(component.stage_id)
            attempt = (
                self.session.get(StageRunModel, component.stage_run_id)
                if component.stage_run_id
                else None
            )
            if (
                stage is None
                or attempt is None
                or (
                    attempt.run_id,
                    attempt.physical_plan_id,
                    attempt.stage_id,
                    attempt.status,
                )
                != (
                    run.run_id,
                    inputs.plan.physical_plan_id,
                    stage.stage_id,
                    "SUCCEEDED",
                )
                or not any(
                    binding.get("output_kind") == "CALCULATOR_RESULT"
                    and binding.get("output_slot_id") == component.key.output_slot_id
                    and binding.get("contract_item_key") == component.key.item_key
                    for binding in stage.output_bindings
                )
            ):
                raise ValueError(
                    "calculator result is outside the selected stage or slot"
                )
            row = self.session.get(CalculatorResultModel, result.calculator_result_id)
            if row is None or (
                row.run_id != run.run_id
                or row.stage_run_id != component.stage_run_id
                or row.contract_item_key != component.key.item_key
                or row.output_slot_id != component.key.output_slot_id
                or type(result).model_validate(row.document) != result
            ):
                raise ValueError(
                    "calculator result reference does not match persisted result"
                )
        existing = {
            (row.metric_key, row.metric_version): row
            for row in self.session.scalars(
                select(MetricResultModel).where(
                    MetricResultModel.run_id == run.run_id,
                    MetricResultModel.schema_version == "2.0",
                )
            )
        }
        expected = {
            (document.metric.key, document.metric.version) for document in documents
        }
        if existing.keys() - expected:
            raise ValueError("persisted metric tree differs from the frozen tree")
        pending = []
        for document in documents:
            row = existing.get((document.metric.key, document.metric.version))
            if row is not None:
                if self.read(row.metric_result_id) != document:
                    raise ValueError(
                        "metric result already persisted with different content"
                    )
                continue
            pending.append(
                MetricResultModel(
                    metric_result_id=document.metric_result_id,
                    run_id=document.run_id,
                    stage_run_id=None,
                    dataset_version_id=document.dataset_version_id,
                    metric_key=document.metric.key,
                    metric_version=document.metric.version,
                    schema_version="2.0",
                    status=document.status,
                    document=document.model_dump(mode="json"),
                )
            )
        self.session.add_all(pending)
        self.session.flush()
        return documents

    @staticmethod
    def _document(inputs: ScoringInputs, metric: MetricScore) -> MetricResultV2:
        score = metric.aggregate.score
        components = tuple(
            MetricComponentV2(
                kind=part.kind,
                ref=part.ref,
                identity=asdict(part.key)
                if isinstance(part.key, ComponentKey)
                else {"node_key": part.key},
                status=part.score.status,
                score=_exact(part.score.value),
                reasons=part.score.reasons,
                blocking=part.score.blocking,
                base_weight=_exact(part.base_weight),
                effective_weight=_exact(part.effective_weight),
                mapping=part.mapping,
                statistics=part.statistics,
                basis=part.basis,
                coverage_basis=part.coverage_basis,
            )
            for part in metric.aggregate.components
        )
        body = {
            "schema_version": "2.0",
            "metric_result_id": str(metric.id),
            "run_id": str(inputs.run.run_id),
            "dataset_version_id": str(inputs.run.dataset_version_id),
            "metric": metric.metric.model_dump(mode="json"),
            "status": score.status,
            "score": _exact(score.value),
            "blocking": score.blocking,
            "reasons": list(score.reasons),
            "components": [part.model_dump(mode="json") for part in components],
            "scorer_version": SCORER_VERSION,
            "input_fingerprint": inputs.run.input_fingerprint,
            "plan_fingerprint": inputs.plan.semantic_fingerprint,
            "contract_fingerprint": inputs.contract.fingerprint,
        }
        return MetricResultV2(**body, fingerprint=semantic_fingerprint(body))

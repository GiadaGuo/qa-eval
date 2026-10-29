from collections.abc import Callable
from datetime import datetime
from operator import eq, ge, gt, le, lt, ne
from typing import Any
from uuid import UUID

from qa_eval.contracts.metrics import MetricObservation, MetricResultV1
from qa_eval.contracts.quality import (
    QualityContractItem,
    QualityContractVersionV1,
    RuleEvaluationV1,
)

IdFactory = Callable[[], UUID]

_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "LT": lt,
    "LTE": le,
    "GT": gt,
    "GTE": ge,
    "EQ": eq,
    "NE": ne,
}


def _missing_evaluation(
    contract: QualityContractVersionV1,
    item: QualityContractItem,
    result: MetricResultV1,
    *,
    reason_code: str,
    id_factory: IdFactory,
    evaluated_at: datetime,
) -> RuleEvaluationV1:
    return RuleEvaluationV1.model_validate(
        {
            "schema_version": "1.0",
            "rule_evaluation_id": id_factory(),
            "run_id": result.run_id,
            "dataset_version_id": result.dataset_version_id,
            "contract_version_id": contract.contract_version_id,
            "contract_item_key": item.key,
            "metric_result_id": result.metric_result_id,
            "metric": item.metric.model_dump(mode="json"),
            "subject": result.subject.model_dump(mode="json"),
            "status": item.on_missing,
            "rule": {
                "strategy": item.rule.strategy,
                "operator": item.rule.operator,
                "expected": item.rule.threshold,
            },
            "reason_code": reason_code,
            "evaluated_at": evaluated_at,
        }
    )


def _find_observation(
    result: MetricResultV1, measurement: str
) -> MetricObservation | None:
    return next(
        (item for item in result.observations if item.key == measurement),
        None,
    )


def evaluate_contract_item(
    contract: QualityContractVersionV1,
    item: QualityContractItem,
    result: MetricResultV1,
    *,
    id_factory: IdFactory,
    evaluated_at: datetime,
) -> RuleEvaluationV1:
    """Apply one immutable user contract item to one MetricResult observation."""

    if contract.status != "PUBLISHED":
        raise ValueError("only PUBLISHED contracts can be evaluated")
    if (
        result.metric.key != item.metric.key
        or result.metric.version != item.metric.version
    ):
        raise ValueError("contract item metric does not match MetricResult")
    if result.subject.scope != item.scope:
        raise ValueError("contract item scope does not match MetricResult subject")

    if result.status == "NOT_APPLICABLE":
        return RuleEvaluationV1.model_validate(
            {
                **_missing_evaluation(
                    contract,
                    item,
                    result,
                    reason_code="NOT_APPLICABLE",
                    id_factory=id_factory,
                    evaluated_at=evaluated_at,
                ).model_dump(mode="json", exclude_none=True),
                "status": "SKIPPED",
            }
        )
    if result.status == "MISSING":
        return _missing_evaluation(
            contract,
            item,
            result,
            reason_code="METRIC_MISSING",
            id_factory=id_factory,
            evaluated_at=evaluated_at,
        )
    if result.status == "ERROR":
        return _missing_evaluation(
            contract,
            item,
            result,
            reason_code="METRIC_ERROR",
            id_factory=id_factory,
            evaluated_at=evaluated_at,
        )
    if result.status == "PARTIAL":
        return _missing_evaluation(
            contract,
            item,
            result,
            reason_code="PARTIAL_RESULT",
            id_factory=id_factory,
            evaluated_at=evaluated_at,
        )

    observation = _find_observation(result, item.metric.measurement)
    if observation is None:
        return _missing_evaluation(
            contract,
            item,
            result,
            reason_code="METRIC_MISSING",
            id_factory=id_factory,
            evaluated_at=evaluated_at,
        )
    if item.rule.strategy == "OBSERVE_ONLY":
        return RuleEvaluationV1.model_validate(
            {
                "schema_version": "1.0",
                "rule_evaluation_id": id_factory(),
                "run_id": result.run_id,
                "dataset_version_id": result.dataset_version_id,
                "contract_version_id": contract.contract_version_id,
                "contract_item_key": item.key,
                "metric_result_id": result.metric_result_id,
                "metric": item.metric.model_dump(mode="json"),
                "subject": result.subject.model_dump(mode="json"),
                "status": "SKIPPED",
                "observed": {
                    "value": observation.value,
                    "value_type": observation.value_type,
                    "unit": observation.unit,
                },
                "rule": {"strategy": "OBSERVE_ONLY"},
                "reason_code": "OBSERVED_ONLY",
                "evaluated_at": evaluated_at,
            }
        )
    try:
        satisfied = _OPERATORS[item.rule.operator](
            observation.value, item.rule.threshold
        )
    except TypeError as exc:
        raise ValueError(
            "contract threshold is incompatible with observation value"
        ) from exc
    status = "PASS" if satisfied else "FAIL"
    reason_code = "THRESHOLD_SATISFIED" if satisfied else "THRESHOLD_VIOLATED"
    return RuleEvaluationV1.model_validate(
        {
            "schema_version": "1.0",
            "rule_evaluation_id": id_factory(),
            "run_id": result.run_id,
            "dataset_version_id": result.dataset_version_id,
            "contract_version_id": contract.contract_version_id,
            "contract_item_key": item.key,
            "metric_result_id": result.metric_result_id,
            "metric": item.metric.model_dump(mode="json"),
            "subject": result.subject.model_dump(mode="json"),
            "status": status,
            "observed": {
                "value": observation.value,
                "value_type": observation.value_type,
                "unit": observation.unit,
            },
            "rule": {
                "strategy": item.rule.strategy,
                "operator": item.rule.operator,
                "expected": item.rule.threshold,
            },
            "reason_code": reason_code,
            "evaluated_at": evaluated_at,
        }
    )


def evaluate_contract(
    contract: QualityContractVersionV1,
    results: list[MetricResultV1],
    *,
    id_factory: IdFactory,
    evaluated_at: datetime,
) -> list[RuleEvaluationV1]:
    results_by_metric = {
        (item.metric.key, item.metric.version): item for item in results
    }
    if len(results_by_metric) != len(results):
        raise ValueError("MetricResults must be unique by metric key and version")
    evaluations = []
    for item in contract.items:
        result = results_by_metric.get((item.metric.key, item.metric.version))
        if result is None:
            raise ValueError(
                f"MetricResult not supplied for contract item {item.key}; "
                "planner coverage must handle not-evaluated metrics"
            )
        evaluations.append(
            evaluate_contract_item(
                contract,
                item,
                result,
                id_factory=id_factory,
                evaluated_at=evaluated_at,
            )
        )
    return evaluations

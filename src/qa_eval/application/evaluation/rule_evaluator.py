from collections.abc import Callable
from datetime import datetime
from operator import eq, ge, gt, le, lt, ne
from typing import Any
from uuid import UUID

from qa_eval.contracts.metrics import MetricObservation, MetricResultV1
from qa_eval.contracts.quality import (
    NativeRuleEvaluationV1,
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

_PARTIAL_POPULATION_REASON_MARKERS = (
    "SAMPLED",
    "MAX_RECORDS",
    "LIMIT_REACHED",
    "SCAN_FAILED",
    "RECORD_FAILED",
    "PARSE_FAILED",
    "TIMEOUT",
)


def _lacks_population_coverage(result: MetricResultV1) -> bool:
    """Detect known partial record coverage, including legacy OK results.

    SemanticCoverage describes supported semantics and is intentionally not
    used here: a Calculator may fully scan its population while documenting
    semantic limitations. This guard only considers explicit execution scope
    and population exclusion reasons that cannot support a population claim.
    """
    if result.status_detail.get("partial_scope") is True:
        return True
    if result.population is None:
        return False
    for exclusion in result.population.exclusions:
        reason = exclusion.reason.upper()
        if exclusion.count and any(
            marker in reason for marker in _PARTIAL_POPULATION_REASON_MARKERS
        ):
            return True
    return False


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
    if _lacks_population_coverage(result):
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
    evaluations = []
    for item in contract.items:
        same_metric = [
            result for result in results
            if (result.metric.key, result.metric.version) == (item.metric.key, item.metric.version)
        ]
        attributes = item.calculation.implementation_constraints.attributes
        if "operator_slot" in attributes:
            candidates = [
                result for result in same_metric
                if result.status_detail.get("calculator", {}).get("operator_slot")
                == attributes["operator_slot"]
            ]
        else:
            # Historical error and missing results may carry no observations.
            candidates = [
                result for result in same_metric
                if any(observation.key == item.metric.measurement for observation in result.observations)
            ] or (same_metric if len(same_metric) == 1 else [])
        if not candidates:
            raise ValueError(
                f"MetricResult not supplied for contract item {item.key}; "
                "planner coverage must handle not-evaluated metrics"
            )
        if len(candidates) > 1:
            raise ValueError(
                f"MetricResult is ambiguous for contract item {item.key}; "
                "the selected measurement must identify one Calculator output"
            )
        result = candidates[0]
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


def evaluate_native_item(contract, item, run, result, *, id_factory, evaluated_at):
    """Apply one published threshold to its explicitly addressed native slot."""
    if contract.status != "PUBLISHED":
        raise ValueError("only PUBLISHED contracts can be evaluated")
    ref = item.statistic_ref
    identity = item.calculation.implementation_constraints.attributes
    calculator = {"key": identity["calculator_key"], "version": ref.calculator_version}
    numerator = denominator = value = None
    status = "NOT_EVALUATED"
    reason = "CALCULATOR_RESULT_MISSING"
    if result is not None:
        if (
            result.run_id != run.run_id
            or result.dataset_version_id != run.dataset_version_id
            or result.input_fingerprint != run.input_fingerprint
            or result.contract_item_key != ref.contract_item_key
            or result.output_slot_id != ref.output_slot_id
            or result.metric != item.metric
            or (result.calculator_key, result.calculator_version)
            != (calculator["key"], calculator["version"])
        ):
            raise ValueError(
                "CalculatorResult does not match the published native rule"
            )
        item.validate_output_schema(result.output_schema)
        statistic = item.statistic
        value = result.statistics.get(statistic.key)
        numerator = result.statistics.get(statistic.numerator_key)
        denominator = result.statistics.get(statistic.denominator_key)
        if denominator == 0:
            reason = "ZERO_DENOMINATOR"
        elif result.status != "OK" or result.status_detail.get("partial_scope") is True:
            reason = (
                f"CALCULATOR_{result.status}"
                if result.status != "OK"
                else "PARTIAL_RESULT"
            )
        elif value is None:
            reason = "STATISTIC_MISSING"
        else:
            passed = _OPERATORS[item.rule.operator](value, item.rule.threshold)
            status = "PASS" if passed else "FAIL"
            reason = "THRESHOLD_SATISFIED" if passed else "THRESHOLD_VIOLATED"
    return NativeRuleEvaluationV1(
        rule_evaluation_id=id_factory(),
        run_id=run.run_id,
        dataset_version_id=run.dataset_version_id,
        contract_version_id=contract.contract_version_id,
        calculator_result_id=result.calculator_result_id
        if result is not None
        else None,
        calculator=calculator,
        metric=item.metric,
        statistic_ref=ref,
        statistic=item.statistic,
        severity=item.severity,
        rule={"operator": item.rule.operator, "expected": item.rule.threshold},
        status=status,
        reason_code=reason,
        metric_value=value,
        numerator=numerator,
        denominator=denominator,
        evaluated_at=evaluated_at,
    )

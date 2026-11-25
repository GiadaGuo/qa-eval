from dataclasses import replace
from fractions import Fraction
from uuid import uuid4

import pytest
from test_scoring_consumer import native as native_case  # noqa: F401
from test_scoring_consumer import persisted  # noqa: F401

from qa_eval.application.evaluation.scoring import MetricAggregator, Scorer
from qa_eval.application.services.metric_aggregation_service import (
    MetricAggregationService,
)
from qa_eval.application.services.scoring_consumer import RecordCounts


@pytest.fixture
def inputs(native_case):  # noqa: F811
    consumer, fixture, selected, *_ = native_case
    return consumer.read(fixture.run_id, selected_attempts=selected, fixture=fixture)


def test_exact_scoring_and_dimension(inputs):
    a, b = Scorer().score(inputs)
    assert (a.score.value, b.score.value) == (Fraction(800, 9), Fraction(90))
    assert (a.coverage, b.coverage) == (Fraction(9, 10), Fraction(1))
    assert b.score.value >= 90
    others = []
    for node in inputs.nodes:
        if node.dimension != "DATA_QUALITY" or node.metric.key == "0202":
            continue
        slot = node.metric.key
        sample = inputs.components[1]
        result = sample.result.model_copy(
            update={
                "metric": node.metric,
                "contract_item_key": slot,
                "output_slot_id": slot,
                "calculator_result_id": uuid4(),
            }
        )
        others.append(
            replace(
                sample,
                key=replace(sample.key, item_key=slot, output_slot_id=slot),
                result=result,
                metric=node.metric,
                counts=RecordCounts(
                    population=20, passed=19, failed=1, unknown=0, not_applicable=0
                ),
            )
        )
    complete = replace(
        inputs, components=(*inputs.components, *others), missing_metrics=()
    )
    first = MetricAggregator().aggregate(complete)
    assert first == MetricAggregator().aggregate(complete)
    metric = next(item for item in first.metrics if item.metric.key == "0202")
    assert metric.aggregate.score.value == Fraction(805, 9)
    assert first.dimensions["DATA_QUALITY"].score.value == Fraction(16975, 180)
    assert first.dimensions["DOCUMENTATION"].score.status == "MISSING"


@pytest.mark.parametrize("status", ["MISSING", "ERROR", "PARTIAL"])
def test_blocked_component_retains_weight(inputs, status):
    left, right = inputs.components
    changed = replace(
        inputs,
        components=(replace(left, status=status, reasons=("INCOMPLETE",)), right),
    )
    result = MetricAggregator().aggregate(changed)
    metric = next(item for item in result.metrics if item.metric.key == "0202")
    assert metric.aggregate.score.status == status
    assert metric.aggregate.score.value is None
    assert all(
        part.effective_weight == Fraction(1, 2) for part in metric.aggregate.components
    )


def test_na_and_zero_denominator(inputs):
    left, right = inputs.components
    na = replace(
        left,
        status="NA",
        configuration=left.configuration.model_copy(
            update={
                "applicability": "NOT_APPLICABLE",
                "applicability_reason": "fixture",
            }
        ),
    )
    changed = replace(
        inputs,
        nodes=tuple(node for node in inputs.nodes if node.metric.key == "0202"),
        components=(na, right),
    )
    dimension = MetricAggregator().aggregate(changed).dimensions["DATA_QUALITY"]
    assert dimension.score.value == 90
    metric = MetricAggregator().aggregate(changed).metrics[0]
    assert metric.aggregate.components[0].effective_weight == 0
    all_na = MetricAggregator().aggregate(replace(changed, components=(na,)))
    assert all_na.dimensions["DATA_QUALITY"].score.status == "NA"
    assert all_na.dimensions["DATA_QUALITY"].score.blocking
    zero = RecordCounts(population=1, passed=0, failed=0, unknown=1, not_applicable=0)
    assert (
        Scorer()
        .score(replace(inputs, components=(replace(left, counts=zero),)))[0]
        .score.blocking
    )


def test_tree_and_reference_guards(inputs):
    left, right = inputs.components
    with pytest.raises(ValueError, match="duplicate component"):
        MetricAggregator().aggregate(replace(inputs, components=(left, left)))
    foreign = right.result.model_copy(update={"run_id": uuid4()})
    with pytest.raises(ValueError, match="ownership"):
        MetricAggregator().aggregate(
            replace(inputs, components=(left, replace(right, result=foreign)))
        )
    node = next(node for node in inputs.nodes if node.metric.key == "0202")
    parent = node.model_copy(
        update={"metric": node.metric.model_copy(update={"key": "0208"})}
    )
    child = node.model_copy(update={"parent_key": "0208"})
    tree = MetricAggregator().aggregate(replace(inputs, nodes=(parent, child)))
    assert tree.metrics[-1].aggregate.score.value == Fraction(805, 9)
    assert tree.metrics[-1].aggregate.components[0].ref == tree.metrics[0].id
    with pytest.raises(ValueError, match="cyclic|both direct"):
        MetricAggregator().aggregate(
            replace(inputs, nodes=(node.model_copy(update={"parent_key": "0202"}),))
        )


def test_metric_storage_is_idempotent_and_formal_gate(native_case, inputs):  # noqa: F811
    consumer, *_ = native_case
    service = MetricAggregationService(consumer.session)
    with pytest.raises(ValueError, match="formal"):
        service.persist(inputs)
    isolated = replace(
        inputs, nodes=tuple(node for node in inputs.nodes if node.metric.key == "0202")
    )
    (first,) = service._persist(isolated)
    consumer.session.commit()
    assert service._persist(isolated) == (first,)
    assert service.read(first.metric_result_id) == first
    assert first.score == "805/9"


def test_explicit_nonrecord_ratio(inputs):
    part = inputs.components[1]
    config = part.configuration.model_copy(
        update={
            "grain": "GROUP",
            "statistic_keys": ["coverage"],
        }
    )
    altered = replace(inputs, components=(replace(part, configuration=config),))
    assert Scorer().score(altered)[0].score.status == "ERROR"
    configured = config.model_copy(update={"mapping": {"transform": "RATIO"}})
    assert (
        Scorer()
        .score(
            replace(
                inputs,
                components=(replace(part, configuration=configured),),
            )
        )[0]
        .score.value
        == 90
    )


def test_0208_parent_execution_excludes_child(inputs):
    a, b = inputs.components
    parent, child = [
        replace(
            part,
            result=None,
            metric=part.metric.model_copy(update={"key": key}),
            status="MISSING",
        )
        for part, key in ((a, "0208"), (b, "02080103"))
    ]
    with pytest.raises(ValueError, match="mutually exclusive"):
        MetricAggregator().aggregate(
            replace(inputs, nodes=(), components=(parent, child))
        )

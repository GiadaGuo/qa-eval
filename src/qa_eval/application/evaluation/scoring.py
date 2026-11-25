"""P1 scoring. Rational arithmetic preserves threshold and aggregation precision."""

from dataclasses import dataclass, replace
from fractions import Fraction
from uuid import UUID, uuid5

from qa_eval.application.services.scoring_consumer import (
    ComponentKey,
    ScoringComponent,
    ScoringInputs,
    Status,
)
from qa_eval.contracts.calculator_results import CalculatorResultV1
from qa_eval.contracts.metrics import VersionedKey
from qa_eval.contracts.quality import NativeEvaluationItemV1

SCORER_VERSION = "1.0"


@dataclass(frozen=True)
class Score:
    status: Status
    value: Fraction | None = None
    reasons: tuple[str, ...] = ()
    blocking: bool = False


@dataclass(frozen=True)
class ComponentScore:
    key: ComponentKey | str
    kind: str
    ref: UUID | None
    score: Score
    base_weight: Fraction = Fraction(0)
    effective_weight: Fraction = Fraction(0)
    mapping: tuple[str, str] | None = None
    statistics: tuple[str, ...] = ()
    basis: tuple[int, int] | None = None
    coverage_basis: tuple[int, int] | None = None

    @property
    def coverage(self) -> Fraction | None:
        if self.coverage_basis and self.coverage_basis[1]:
            return Fraction(*self.coverage_basis)
        return None


class Scorer:
    def score(self, inputs: ScoringInputs) -> tuple[ComponentScore, ...]:
        return tuple(
            self._score(component, inputs.coverage_policy(component.key))
            for component in inputs.components
        )

    @staticmethod
    def _score(component: ScoringComponent, policy) -> ComponentScore:
        config = component.configuration
        result = component.result
        counts = component.counts
        entry = ComponentScore(
            key=component.key,
            kind="CALCULATOR_RESULT",
            ref=result.calculator_result_id
            if isinstance(result, CalculatorResultV1)
            else None,
            score=Score(component.status, reasons=component.reasons, blocking=True),
            mapping=(config.mapping_id, config.mapping_version) if config else None,
            statistics=tuple(config.statistic_keys) if config else (),
            coverage_basis=counts.coverage_basis if counts else None,
        )
        if component.status == "NA":
            if (
                component.source == "NATIVE"
                and entry.ref
                and config
                and config.applicability == "NOT_APPLICABLE"
                and config.applicability_reason
            ):
                return replace(
                    entry, score=Score("NA", reasons=(config.applicability_reason,))
                )
            return replace(
                entry, score=Score("ERROR", reasons=("UNPROVEN_NA",), blocking=True)
            )
        if component.status in {"MISSING", "ERROR"}:
            return entry
        if component.source == "LEGACY" or config is None or entry.ref is None:
            return replace(
                entry,
                score=Score(
                    "PARTIAL",
                    reasons=component.reasons + ("SCORING_INPUT_UNAVAILABLE",),
                    blocking=True,
                ),
            )
        reasons = component.reasons
        blocking = component.status == "PARTIAL" and (
            not reasons or bool(set(reasons) - {"UNKNOWN_RECORDS"})
        )
        if config.grain == "RECORD":
            if counts is None or not counts.coverage_basis[0]:
                return replace(
                    entry,
                    score=Score(
                        "PARTIAL",
                        reasons=(*reasons, "ZERO_OR_MISSING_SCORE_DENOMINATOR"),
                        blocking=True,
                    ),
                )
            basis = counts.passed, counts.coverage_basis[0]
            entry = replace(entry, basis=basis)
            ratio = Fraction(*basis)
            if counts.unknown and "UNKNOWN_RECORDS" not in reasons:
                reasons += ("UNKNOWN_RECORDS",)
        else:
            # Explicit versioned DSL. A mapping author must opt in.
            transform = config.mapping.get("transform")
            if config.mapping_version != "1" or transform not in (
                "RATIO",
                "INVERSE_RATIO",
            ):
                return replace(
                    entry,
                    score=Score(
                        "ERROR",
                        reasons=(*reasons, "UNSUPPORTED_SCORING_MAPPING"),
                        blocking=True,
                    ),
                )
            definition = next(
                (
                    item
                    for item in result.output_schema.statistics
                    if config.statistic_keys == [item.key] and item.kind == "ratio"
                ),
                None,
            )
            if definition is None or component.statistics is None:
                return replace(
                    entry,
                    score=Score(
                        "ERROR",
                        reasons=(*reasons, "INVALID_RATIO_MAPPING"),
                        blocking=True,
                    ),
                )
            numerator = component.statistics.get(definition.numerator_key)
            denominator = component.statistics.get(definition.denominator_key)
            if denominator == 0:
                return replace(
                    entry,
                    score=Score(
                        "PARTIAL",
                        reasons=(*reasons, "ZERO_SCORE_DENOMINATOR"),
                        blocking=True,
                    ),
                )
            if (
                type(numerator) is not int
                or type(denominator) is not int
                or not 0 <= numerator <= denominator
            ):
                return replace(
                    entry,
                    score=Score(
                        "ERROR", reasons=("INVALID_RATIO_COUNTS",), blocking=True
                    ),
                )
            entry = replace(entry, basis=(numerator, denominator))
            ratio = Fraction(numerator, denominator)
            if transform == "INVERSE_RATIO":
                ratio = 1 - ratio
        threshold = policy.coverage_min if policy else config.coverage_min
        if config.grain == "RECORD" and threshold is None:
            reasons += ("COVERAGE_THRESHOLD_UNCONFIRMED",)
        elif config.grain == "RECORD" and entry.coverage < Fraction(str(threshold)):
            reasons += ("COVERAGE_BELOW_MINIMUM",)
        status = (
            "PARTIAL"
            if component.status == "PARTIAL" or "UNKNOWN_RECORDS" in reasons
            else "OK"
        )
        return replace(entry, score=Score(status, 100 * ratio, reasons, blocking))


@dataclass(frozen=True)
class Aggregate:
    score: Score
    components: tuple[ComponentScore, ...]


@dataclass(frozen=True)
class MetricScore:
    id: UUID
    metric: VersionedKey
    aggregate: Aggregate


@dataclass(frozen=True)
class Aggregation:
    metrics: tuple[MetricScore, ...]
    dimensions: dict[str, Aggregate]
    diagnostics: tuple[str, ...]


class MetricAggregator:
    def aggregate(self, inputs: ScoringInputs) -> Aggregation:
        scores = Scorer().score(inputs)
        nodes = {node.metric.key: node for node in inputs.nodes}
        if len(nodes) != len(inputs.nodes):
            raise ValueError("duplicate metric node")
        if len({part.key.output_slot_id for part in scores}) != len(scores):
            raise ValueError("duplicate component")
        refs = [part.ref for part in scores if part.ref is not None]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate calculator result")
        children: dict[str | None, list[str]] = {}
        for key, node in nodes.items():
            parent = node.parent_key
            if parent is not None and (
                parent not in nodes or nodes[parent].dimension != node.dimension
            ):
                raise ValueError("invalid metric parent")
            children.setdefault(parent, []).append(key)
        direct: dict[str, list[ComponentScore]] = {}
        for component, score in zip(inputs.components, scores, strict=True):
            result = component.result
            key = component.metric.key
            if result is not None and (
                result.run_id != inputs.run.run_id
                or result.metric != component.metric
                or result.stage_run_id != component.stage_run_id
            ):
                raise ValueError("component result ownership mismatch")
            if isinstance(result, CalculatorResultV1) and (
                (result.contract_item_key, result.output_slot_id)
                != (component.key.item_key, component.key.output_slot_id)
                or NativeEvaluationItemV1.schema_fingerprint(result.output_schema)
                != component.key.schema_fingerprint
                or result.plan_fingerprint != inputs.plan.semantic_fingerprint
                or result.input_fingerprint != inputs.run.input_fingerprint
                or result.dataset_version_id != inputs.run.dataset_version_id
            ):
                raise ValueError("component slot or frozen identity mismatch")
            if nodes and (key not in nodes or nodes[key].metric != component.metric):
                raise ValueError("component outside frozen metric tree")
            direct.setdefault(key, []).append(score)
        if "0208" in direct and any(key.startswith("020801") for key in direct):
            raise ValueError("0208 parent and child execution are mutually exclusive")
        metrics: dict[str, MetricScore] = {}
        visiting = set()

        def visit(key: str) -> MetricScore:
            if key in metrics:
                return metrics[key]
            if key in visiting:
                raise ValueError("cyclic metric tree")
            visiting.add(key)
            descendants = children.get(key, ())
            if descendants and key in direct:
                raise ValueError("metric has both direct execution and child results")
            parts = tuple(as_component(visit(child)) for child in sorted(descendants))
            if not parts:
                parts = tuple(
                    sorted(direct.get(key, ()), key=lambda part: repr(part.key))
                )
            metric = nodes[key].metric
            value = MetricScore(
                uuid5(inputs.run.run_id, f"metric/2.0/{metric.key}/{metric.version}"),
                metric,
                self._combine(parts),
            )
            metrics[key] = value
            visiting.remove(key)
            return value

        for key in sorted(nodes):
            visit(key)
        dimensions: dict[str, list[ComponentScore]] = {}
        for root in sorted(children.get(None, ())):
            dimensions.setdefault(nodes[root].dimension, []).append(
                as_component(metrics[root])
            )
        diagnostics = inputs.blockers + tuple(
            f"MISSING_METRIC:{item.key}" for item in inputs.missing_metrics
        )
        return Aggregation(
            tuple(metrics.values()),
            {
                key: self._combine(tuple(parts), dimension=True)
                for key, parts in dimensions.items()
            },
            diagnostics,
        )

    @staticmethod
    def _combine(
        parts: tuple[ComponentScore, ...], *, dimension: bool = False
    ) -> Aggregate:
        if not parts:
            return Aggregate(
                Score("MISSING", reasons=("NO_CONFIGURED_COMPONENTS",), blocking=True),
                (),
            )
        active = [part for part in parts if part.score.status != "NA"]
        weighted = tuple(
            replace(
                part,
                base_weight=Fraction(1, len(parts)),
                effective_weight=Fraction(1, len(active))
                if part.score.status != "NA"
                else Fraction(0),
            )
            for part in parts
        )
        reasons = tuple(
            dict.fromkeys(reason for part in parts for reason in part.score.reasons)
        )
        if not active:
            return Aggregate(
                Score(
                    "NA",
                    reasons=reasons
                    + (("REQUIRED_DIMENSION_ALL_NA",) if dimension else ()),
                    blocking=dimension,
                ),
                weighted,
            )
        rank = {"OK": 0, "PARTIAL": 1, "MISSING": 2, "ERROR": 3}
        status = max((part.score.status for part in active), key=rank.__getitem__)
        blocking = any(
            part.score.blocking or part.score.value is None for part in active
        )
        value = (
            None if blocking else sum(part.score.value for part in active) / len(active)
        )
        return Aggregate(Score(status, value, reasons, blocking), weighted)


def as_component(metric: MetricScore) -> ComponentScore:
    return ComponentScore(
        metric.metric.key, "METRIC_RESULT", metric.id, metric.aggregate.score
    )


def score_document_aspects(status, statistics):
    """Score a required aspect result on the shared 0–100 scale."""
    if status != "OK":
        return Score(status, reasons=(f"DOCUMENT_{status}",), blocking=True)
    a = statistics.get("satisfied_aspect_count")
    b = statistics.get("required_aspect_count")
    if type(a) is not int or type(b) is not int or not 0 <= a <= b or b == 0:
        return Score("ERROR", reasons=("INVALID_DOCUMENT_COUNTS",), blocking=True)
    return Score("OK", 100 * Fraction(a, b))


def weighted_required_scores(parts):
    """Keep frozen weights even when a required input cannot be scored."""
    reasons = tuple(
        dict.fromkeys(reason for part in parts for reason in part.score.reasons)
    )
    blocking = any(part.score.blocking or part.score.value is None for part in parts)
    if blocking:
        return Aggregate(Score("INDETERMINATE", reasons=reasons, blocking=True), parts)
    score = sum((part.score.value * part.base_weight for part in parts), Fraction(0))
    return Aggregate(Score("OK", score, reasons), parts)

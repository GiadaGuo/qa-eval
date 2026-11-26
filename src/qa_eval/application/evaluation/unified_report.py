"""Unified report projection and standard verdict."""

from fractions import Fraction
from typing import Literal
from uuid import UUID

from pydantic import Field

from qa_eval.application.evaluation.scoring import (
    Aggregate,
    ComponentScore,
    MetricAggregator,
    Score,
)
from qa_eval.application.services.scoring_consumer import ScoringInputs
from qa_eval.contracts.base import VersionTarget, WireModel
from qa_eval.contracts.document import DocumentQualityReportV1

Verdict = Literal["PASS", "FAIL", "INDETERMINATE"]
DOCUMENT_KEYS = ("0101", "0102", "0103", "0104")


class DocumentMapping(WireModel):
    version: Literal["1"]
    report_id: UUID
    document_version_id: UUID
    dataset_id: UUID
    report_fingerprint: str
    rubric_version: str
    rubric_sha256: str
    evaluator_version: str
    observations: dict[str, str]
    approved_na: dict[str, str] = Field(default_factory=dict)


class BusinessGate(WireModel):
    approval_ref: str
    rule_evaluation_id: UUID
    verdict: Verdict


class ReportCoverage(WireModel):
    ratio: str | None = None
    numerator: int | None = None
    denominator: int | None = None
    threshold: float | None = None
    source: str | None = None
    verdict: Literal["PASS", "FAIL", "INDETERMINATE", "NA"]


def coverage_gate(component, policy) -> ReportCoverage:
    counts = component.counts
    known, total = counts.coverage_basis if counts else (None, None)
    configuration = getattr(component, "configuration", None)
    approved_na = False
    if configuration is not None and configuration.grain != "RECORD":
        approved_na = bool(
            configuration.coverage_na_approval and not configuration.coverage_statistic
        )
        if (
            configuration.coverage_statistic
            and component.statistics
            and component.result
        ):
            schema = getattr(component.result, "output_schema", None)
            definition = (
                next(
                    (
                        item
                        for item in schema.statistics
                        if item.kind == "ratio"
                        and item.key == configuration.coverage_statistic
                    ),
                    None,
                )
                if schema
                else None
            )
            if definition:
                numerator = component.statistics.get(definition.numerator_key)
                denominator = component.statistics.get(definition.denominator_key)
                if (
                    type(numerator) is int
                    and type(denominator) is int
                    and 0 <= numerator <= denominator
                ):
                    known, total = numerator, denominator
    ratio = Fraction(known, total) if total else None
    verdict = "INDETERMINATE"
    if component.status == "NA" or approved_na:
        verdict = "NA"
    elif ratio is not None and policy is not None:
        verdict = "PASS" if ratio >= Fraction(str(policy.coverage_min)) else "FAIL"
    return ReportCoverage(
        ratio=str(ratio) if ratio is not None else None,
        numerator=known,
        denominator=total,
        threshold=policy.coverage_min if policy else None,
        source=policy.source if policy else None,
        verdict=verdict,
    )


class ReportMetric(WireModel):
    key: str
    ref: UUID | None = None
    path: str | None = None
    evidence_paths: tuple[str, ...] = ()
    score: str | None = None
    status: str
    weight: str
    base_weight: str
    coverage: dict[str, tuple[int, int]]
    reasons: tuple[str, ...]
    satisfied_aspect_count: int | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    required_aspect_count: int | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    stage_run_id: UUID | None = Field(default=None, exclude_if=lambda v: v is None)
    checks: tuple[dict, ...] = Field(default=(), exclude_if=lambda v: not v)


class ReportDimension(WireModel):
    key: str
    score: str | None
    status: str
    verdict: Verdict
    threshold: float = Field(default=90, ge=0, le=100)
    metrics: tuple[ReportMetric, ...]
    reasons: tuple[str, ...]


class ReportSnapshotRef(WireModel):
    report_id: UUID
    run_id: UUID
    version_id: UUID
    contract_version_id: UUID
    fingerprint: str


class QualityReportV2(VersionTarget):
    schema_version: Literal["2.0"] = "2.0"
    report_id: UUID
    run_id: UUID
    contract_version_id: UUID
    mode: Literal["BASIC", "DOCUMENT"] = "BASIC"
    task_type: Literal["DATASET", "SINGLE_METRIC", "DOCUMENT"] | None = Field(default=None, exclude_if=lambda value: value is None)
    contract_fingerprint: str
    plan_fingerprint: str
    input_fingerprint: str
    fingerprint: str
    finalized_at: str
    dimensions: tuple[ReportDimension, ...]
    overall_verdict: Verdict
    gate_reasons: tuple[str, ...]
    issue_counts: dict[str, int]
    business_gate_results: tuple[BusinessGate, ...] = ()
    document_reference: DocumentMapping | None = None
    coverage_gates: dict[str, ReportCoverage] = Field(default_factory=dict)
    document_snapshot: ReportSnapshotRef | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    dataset_snapshot: ReportSnapshotRef | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    scoring_profile: dict | None = Field(default=None, exclude_if=lambda v: v is None)


def standard_verdict(score: Score, threshold: float = 90) -> Verdict:
    if (
        score.blocking
        or score.value is None
        or "COVERAGE_THRESHOLD_UNCONFIRMED" in score.reasons
    ):
        return "INDETERMINATE"
    return (
        "FAIL"
        if score.value < Fraction(str(threshold))
        or "COVERAGE_BELOW_MINIMUM" in score.reasons
        else "PASS"
    )


def project_document(
    report: DocumentQualityReportV1 | None,
    mapping: DocumentMapping | None,
) -> ReportDimension:
    parts = []
    locations = {}
    for key in DOCUMENT_KEYS:
        score = Score(
            "MISSING", reasons=("DOCUMENT_REPORT_OR_MAPPING_MISSING",), blocking=True
        )
        if report and mapping and key in mapping.observations:
            for j, metric in enumerate(report.metrics):
                if metric.metric.key != key:
                    continue
                path = f"/metrics/{j}"
                locations[key] = (
                    path,
                    tuple(
                        f"{path}/items/{i}/document_evidence"
                        for i, item in enumerate(metric.items)
                        if item.document_evidence
                    ),
                )
                if metric.status == "ERROR":
                    score = Score(
                        "ERROR", reasons=("DOCUMENT_METRIC_ERROR",), blocking=True
                    )
                    break
                if mapping.approved_na.get(key):
                    score = Score("NA", reasons=(mapping.approved_na[key],))
                    break
                values = [
                    obs.value
                    for obs in metric.observations
                    if obs.key == mapping.observations[key]
                ]
                if (
                    len(values) == 1
                    and type(values[0]) in (int, float)
                    and 0 <= values[0] <= 100
                ):
                    score = Score("OK", Fraction(str(values[0])))
                break
        parts.append(
            ComponentScore(
                key, "METRIC_RESULT", report.report_id if report else None, score
            )
        )
    return dimension(
        "DOCUMENTATION",
        MetricAggregator._combine(tuple(parts), dimension=True),
        locations,
    )


def dimension(
    key: str,
    result: Aggregate,
    locations: dict[str, tuple[str, tuple[str, ...]]] | None = None,
    coverage: dict[str, dict[str, tuple[int, int]]] | None = None,
    *,
    threshold: float = 90,
) -> ReportDimension:
    output = []
    for part in result.components:
        node = str(part.key)
        path, evidence = (locations or {}).get(node, (None, ()))
        output.append(
            ReportMetric(
                key=node,
                ref=part.ref,
                path=path,
                evidence_paths=evidence,
                score=str(part.score.value) if part.score.value is not None else None,
                status=part.score.status,
                weight=str(part.effective_weight),
                base_weight=str(part.base_weight),
                coverage=(coverage or {}).get(node, {}),
                reasons=part.score.reasons,
            )
        )
    score = result.score
    return ReportDimension(
        key=key,
        score=str(score.value) if score.value is not None else None,
        status=score.status,
        verdict=standard_verdict(score, threshold),
        threshold=threshold,
        metrics=tuple(output),
        reasons=score.reasons,
    )


def report_dimensions(
    inputs: ScoringInputs,
    report: DocumentQualityReportV1 | None,
    mapping: DocumentMapping | None,
) -> tuple[ReportDimension, ReportDimension]:
    evaluation = MetricAggregator().aggregate(inputs)
    metrics = {metric.id: metric for metric in evaluation.metrics}

    def bases(ref):
        merged = {}
        for part in metrics[ref].aggregate.components:
            if part.kind == "METRIC_RESULT":
                merged.update(bases(part.ref))
            elif part.coverage_basis is not None:
                merged[part.key.output_slot_id] = part.coverage_basis
        return merged

    data = evaluation.dimensions.get(
        "DATA_QUALITY",
        Aggregate(
            Score("MISSING", reasons=("DATA_QUALITY_MISSING",), blocking=True),
            (),
        ),
    )
    return project_document(report, mapping), dimension(
        "DATA_QUALITY",
        data,
        coverage={part.key: bases(part.ref) for part in data.components},
    )


def document_profile(weights):
    from qa_eval.contracts.scoring import ScoringProfileV1

    return ScoringProfileV1(
        profile_id="DOCUMENTATION",
        profile_version="1.0.0",
        profile_type="OFFICIAL_GUIDE",
        aggregation_method="WEIGHTED_MEAN",
        disclaimer="0101–0104 use the published document Contract weights.",
        metrics=[
            {
                "metric_id": key,
                "dimension_id": key,
                "measurement_key": "completeness_ratio",
                "scoring_mapping_version": "1.0.0",
                "scoring_method": "RATIO_HIGHER_IS_BETTER",
                "direction": "HIGHER_IS_BETTER",
                "valid_range": {"minimum": 0, "maximum": 1},
                "weight": weights[key],
                "eligibility_policy": "FULL_RESULT",
                "aggregation_method": "DIRECT",
            }
            for key in DOCUMENT_KEYS
        ],
    )

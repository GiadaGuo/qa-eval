from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityReportV1_1
from qa_eval.contracts.scoring import (
    MetricScoreContribution,
    OperationalRadarAxis,
    OperationalScoringReportV1,
    ScoringProfileMetric,
    ScoringProfileV1,
)
from qa_eval.infrastructure.uuid7 import uuid7

DEFAULT_OPERATIONAL_PROFILE_ID = "DATA_QUALITY_MVP_SCORE_V1"
_PROFILE_FILES = {
    DEFAULT_OPERATIONAL_PROFILE_ID: "data-quality-mvp-score-v1.json",
}


def load_scoring_profile(profile_id: str = DEFAULT_OPERATIONAL_PROFILE_ID) -> ScoringProfileV1:
    filename = _PROFILE_FILES.get(profile_id)
    if filename is None:
        raise ValueError(f"Unknown scoring profile: {profile_id}")
    root = Path(__file__).resolve().parents[4]
    payload = json.loads((root / "configs" / "scoring" / filename).read_text())
    return ScoringProfileV1.model_validate(payload)


def map_metric_score(value: object, metric: ScoringProfileMetric) -> tuple[float | None, str | None]:
    if metric.scoring_method == "NONE":
        return None, "OBSERVE_ONLY" if metric.eligibility_policy == "OBSERVE_ONLY" else "NOT_APPLICABLE"
    if value is None:
        return None, "MEASUREMENT_MISSING"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "MEASUREMENT_NOT_NUMERIC"
    valid_range = metric.valid_range
    if valid_range is None:
        return None, "VALID_RANGE_MISSING"
    numeric = float(value)
    if numeric < valid_range.minimum or numeric > valid_range.maximum:
        return None, "MEASUREMENT_OUT_OF_RANGE"
    if metric.scoring_method == "RATIO_HIGHER_IS_BETTER":
        score = numeric * 100
    elif metric.scoring_method == "RATIO_LOWER_IS_BETTER":
        score = (1 - numeric) * 100
    else:  # pragma: no cover - protected by the canonical profile contract
        return None, "SCORING_METHOD_UNSUPPORTED"
    return round(score, 4), None


def _contribution(
    profile_metric: ScoringProfileMetric,
    result: MetricResultV1 | None,
    stage: dict | None,
) -> MetricScoreContribution:
    reasons: list[str] = []
    observation = None if result is None else next(
        (item for item in result.observations if item.key == profile_metric.measurement_key),
        None,
    )
    raw_value = None if observation is None else observation.value
    score, mapping_error = map_metric_score(raw_value, profile_metric)
    if result is None:
        reasons.append("METRIC_RESULT_MISSING")
    elif profile_metric.eligibility_policy == "NOT_APPLICABLE":
        reasons.append("NOT_APPLICABLE")
    elif profile_metric.eligibility_policy == "OBSERVE_ONLY":
        reasons.append("UNCALIBRATED" if profile_metric.metric_id == "02080101" else "OBSERVE_ONLY")
    elif profile_metric.eligibility_policy == "FULL_RESULT" and result.status != "OK":
        reasons.append(f"RESULT_STATUS_{result.status}")
    elif profile_metric.eligibility_policy == "SAMPLED_ESTIMATE" and result.status != "PARTIAL":
        reasons.append(f"SAMPLED_ESTIMATE_REQUIRES_PARTIAL_RESULT_{result.status}")
    if mapping_error and mapping_error not in {"OBSERVE_ONLY", "NOT_APPLICABLE"}:
        reasons.append(mapping_error)

    contribution_type = (
        "SAMPLED_ESTIMATE"
        if profile_metric.eligibility_policy == "SAMPLED_ESTIMATE" and not reasons
        else "FULL_RESULT"
        if profile_metric.eligibility_policy == "FULL_RESULT" and not reasons
        else "EXCLUDED"
    )
    if contribution_type == "EXCLUDED":
        score = None

    sample_size = population_size = None
    sampling_method = None
    coverage_ratio = None
    missing_sampling_metadata: list[str] = []
    if profile_metric.eligibility_policy == "SAMPLED_ESTIMATE":
        sample_size = None if result is None or result.population is None else result.population.evaluated_count
        stats = {} if stage is None else stage.get("stats", {})
        population_size = stats.get("source_records")
        sampling_method = stats.get("sampling_method")
        if sample_size is None:
            missing_sampling_metadata.append("sample_size")
        if population_size is None:
            missing_sampling_metadata.append("population_size")
        elif population_size > 0 and sample_size is not None:
            coverage_ratio = sample_size / population_size
        if sampling_method is None:
            missing_sampling_metadata.append("sampling_method")

    return MetricScoreContribution(
        metric_id=profile_metric.metric_id,
        measurement_key=profile_metric.measurement_key,
        raw_value=raw_value if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) else None,
        score=score,
        sampled_estimate_score=(score if contribution_type == "SAMPLED_ESTIMATE" else None),
        contribution_type=contribution_type,
        weight=profile_metric.weight,
        scoring_mapping_version=profile_metric.scoring_mapping_version,
        sample_size=sample_size,
        population_size=population_size,
        sampling_method=sampling_method,
        coverage_ratio=coverage_ratio,
        missing_sampling_metadata=missing_sampling_metadata,
        exclusion_reasons=reasons,
    )


def _weighted_score(items: list[MetricScoreContribution]) -> float | None:
    included = [item for item in items if item.score is not None]
    weight = sum(item.weight for item in included)
    if weight == 0:
        return None
    return round(sum(item.score * item.weight for item in included if item.score is not None) / weight, 4)


def build_operational_scoring_report(
    base_report: QualityReportV1_1,
    results: list[MetricResultV1],
    stage_runs: dict[UUID, dict],
    profile: ScoringProfileV1,
    *,
    scored_report_id: UUID | None = None,
    generated_at: datetime | None = None,
) -> OperationalScoringReportV1:
    result_by_key = {item.metric.key: item for item in results}
    official_by_axis = {item.dimension: item for item in base_report.dimension_scores}
    contributions_by_axis: dict[str, list[MetricScoreContribution]] = {f"020{i}": [] for i in range(1, 9)}
    configured_by_axis: dict[str, list[ScoringProfileMetric]] = {f"020{i}": [] for i in range(1, 9)}
    for item in profile.metrics:
        result = result_by_key.get(item.metric_id)
        stage = None if result is None else stage_runs.get(result.stage_run_id)
        configured_by_axis[item.dimension_id].append(item)
        contributions_by_axis[item.dimension_id].append(_contribution(item, result, stage))

    axes: list[OperationalRadarAxis] = []
    for dimension in [f"020{i}" for i in range(1, 9)]:
        configured = configured_by_axis[dimension]
        contributions = contributions_by_axis[dimension]
        eligible_weight = sum(item.weight for item in configured if item.scoring_method != "NONE")
        included = [item for item in contributions if item.score is not None]
        full = [item for item in included if item.contribution_type == "FULL_RESULT"]
        sampled = [item for item in included if item.contribution_type == "SAMPLED_ESTIMATE"]
        estimated_weight = sum(item.weight for item in included)
        full_weight = sum(item.weight for item in full)
        estimated_score = _weighted_score(included)
        deterministic_score = _weighted_score(full)
        if estimated_score is None:
            estimate_type = "UNAVAILABLE"
        elif sampled and full:
            estimate_type = "MIXED"
        elif sampled:
            estimate_type = "SAMPLED_ESTIMATE"
        else:
            estimate_type = "FULL_RESULT"
        official = official_by_axis.get(dimension)
        sample_sizes = [item.sample_size for item in sampled if item.sample_size is not None]
        population_sizes = [item.population_size for item in sampled if item.population_size is not None]
        axes.append(
            OperationalRadarAxis(
                dimension=dimension,
                official_score=None if official is None else official.score,
                official_coverage=0 if official is None or official.coverage is None else official.coverage,
                official_status="UNAVAILABLE" if official is None else official.status,
                estimated_score=estimated_score,
                estimated_coverage=0 if eligible_weight == 0 else estimated_weight / eligible_weight,
                deterministic_score=deterministic_score,
                deterministic_coverage=0 if eligible_weight == 0 else full_weight / eligible_weight,
                estimate_type=estimate_type,
                effective_weight=estimated_weight,
                applicable_weight=eligible_weight,
                sample_size=min(sample_sizes) if sample_sizes else None,
                population_size=max(population_sizes) if population_sizes else None,
                contributing_metrics=included,
                excluded_metrics=[item for item in contributions if item.score is None],
            )
        )

    available_axes = [item for item in axes if item.estimated_score is not None]
    eligible_axis_count = sum(any(item.scoring_method != "NONE" for item in configured_by_axis[axis]) for axis in configured_by_axis)
    return OperationalScoringReportV1(
        scored_report_id=scored_report_id or uuid7(),
        base_report_id=base_report.report_id,
        run_id=base_report.run_id,
        scoring_profile_id=profile.profile_id,
        scoring_profile_version=profile.profile_version,
        profile_type=profile.profile_type,
        overall_estimated_score=(
            None
            if not available_axes
            else round(sum(item.estimated_score for item in available_axes if item.estimated_score is not None) / len(available_axes), 4)
        ),
        estimated_dimension_coverage=0 if eligible_axis_count == 0 else len(available_axes) / eligible_axis_count,
        radar=axes,
        disclaimer=profile.disclaimer,
        generated_at=generated_at or datetime.now(UTC),
    )

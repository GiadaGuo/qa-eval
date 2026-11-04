from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from qa_eval.contracts.base import WireModel


class ScoreRange(WireModel):
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> "ScoreRange":
        if self.minimum >= self.maximum:
            raise ValueError("valid_range minimum must be less than maximum")
        return self


class ScoringProfileMetric(WireModel):
    metric_id: str = Field(min_length=1)
    measurement_key: str = Field(min_length=1)
    scoring_mapping_version: str = Field(min_length=1)
    scoring_method: Literal["RATIO_HIGHER_IS_BETTER", "RATIO_LOWER_IS_BETTER", "NONE"]
    direction: Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER", "UNSCORED"]
    valid_range: ScoreRange | None = None
    weight: float = Field(gt=0)
    dimension_id: str = Field(pattern=r"^020[1-8]$")
    eligibility_policy: Literal["FULL_RESULT", "SAMPLED_ESTIMATE", "OBSERVE_ONLY", "NOT_APPLICABLE"]
    aggregation_method: Literal["DIRECT", "WEIGHTED_MEAN"]

    @model_validator(mode="after")
    def mapping_is_consistent(self) -> "ScoringProfileMetric":
        if self.scoring_method == "NONE":
            if self.direction != "UNSCORED" or self.valid_range is not None:
                raise ValueError("unscored metrics cannot declare direction or range")
        elif self.valid_range is None:
            raise ValueError("scored metrics require a valid_range")
        elif (self.valid_range.minimum, self.valid_range.maximum) != (0, 1):
            raise ValueError("ratio scoring methods require the explicit [0, 1] range")
        return self


class ScoringProfileV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    profile_type: Literal["MVP_OPERATIONAL", "OFFICIAL_GUIDE"]
    aggregation_method: Literal["WEIGHTED_MEAN"]
    metrics: list[ScoringProfileMetric] = Field(min_length=1)
    disclaimer: str = Field(min_length=1)

    @model_validator(mode="after")
    def metric_ids_are_unique(self) -> "ScoringProfileV1":
        ids = [item.metric_id for item in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("scoring profile metric ids must be unique")
        return self


class MetricScoreContribution(WireModel):
    metric_id: str
    measurement_key: str
    raw_value: float | None = None
    score: float | None = Field(default=None, ge=0, le=100)
    sampled_estimate_score: float | None = Field(default=None, ge=0, le=100)
    contribution_type: Literal["FULL_RESULT", "SAMPLED_ESTIMATE", "EXCLUDED"]
    weight: float = Field(gt=0)
    scoring_mapping_version: str
    sample_size: int | None = Field(default=None, ge=0)
    population_size: int | None = Field(default=None, ge=0)
    sampling_method: str | None = None
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    missing_sampling_metadata: list[str] = Field(default_factory=list)
    exclusion_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def restore_sampled_estimate_alias(cls, value):
        if isinstance(value, dict) and "sampled_estimate_score" not in value:
            value = {
                **value,
                "sampled_estimate_score": (
                    value.get("score")
                    if value.get("contribution_type") == "SAMPLED_ESTIMATE"
                    else None
                ),
            }
        return value


class OperationalRadarAxis(WireModel):
    dimension: str = Field(pattern=r"^020[1-8]$")
    official_score: float | None = Field(default=None, ge=0, le=100)
    official_coverage: float = Field(ge=0, le=1)
    official_status: Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]
    estimated_score: float | None = Field(default=None, ge=0, le=100)
    estimated_coverage: float = Field(ge=0, le=1)
    deterministic_score: float | None = Field(default=None, ge=0, le=100)
    deterministic_coverage: float = Field(ge=0, le=1)
    estimate_type: Literal["FULL_RESULT", "SAMPLED_ESTIMATE", "MIXED", "UNAVAILABLE"]
    effective_weight: float = Field(ge=0)
    applicable_weight: float = Field(ge=0)
    sample_size: int | None = Field(default=None, ge=0)
    population_size: int | None = Field(default=None, ge=0)
    contributing_metrics: list[MetricScoreContribution] = Field(default_factory=list)
    excluded_metrics: list[MetricScoreContribution] = Field(default_factory=list)


class OperationalScoringReportV1(WireModel):
    report_schema_version: Literal["1.2-mvp"] = "1.2-mvp"
    scored_report_id: UUID
    base_report_id: UUID
    run_id: UUID
    scoring_profile_id: str
    scoring_profile_version: str
    profile_type: Literal["MVP_OPERATIONAL", "OFFICIAL_GUIDE"]
    overall_estimated_score: float | None = Field(default=None, ge=0, le=100)
    estimated_dimension_coverage: float = Field(ge=0, le=1)
    radar: list[OperationalRadarAxis] = Field(min_length=8, max_length=8)
    disclaimer: str
    generated_at: datetime

    @model_validator(mode="after")
    def axes_are_ordered(self) -> "OperationalScoringReportV1":
        if [item.dimension for item in self.radar] != [f"020{i}" for i in range(1, 9)]:
            raise ValueError("operational radar must contain ordered dimensions 0201 through 0208")
        return self

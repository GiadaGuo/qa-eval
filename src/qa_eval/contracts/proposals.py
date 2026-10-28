from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.quality import Severity
from qa_eval.contracts.registry import MetricCalculatorBindingV1

ApplicabilityStatus = Literal[
    "APPLICABLE", "NOT_APPLICABLE", "NEEDS_INPUT", "UNAVAILABLE"
]


class ApplicabilityFindingV1(WireModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: JsonObject = Field(default_factory=dict)


class ApplicabilityDecisionV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    status: ApplicabilityStatus
    findings: list[ApplicabilityFindingV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def non_applicable_status_has_findings(self) -> "ApplicabilityDecisionV1":
        if self.status != "APPLICABLE" and not self.findings:
            raise ValueError(f"{self.status} requires at least one finding")
        return self


class ProposalMetricRef(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    measurement: str = Field(min_length=1)


class ProposalCalculatorRef(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ProposalRuleV1(WireModel):
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NE"]
    threshold: Any
    severity: Severity
    on_missing: Literal["UNKNOWN", "SKIPPED"]


class EvaluationProposalItemV1(WireModel):
    key: str = Field(min_length=1)
    metric: ProposalMetricRef
    candidates: list[ProposalCalculatorRef] = Field(min_length=1)
    selected_calculator: ProposalCalculatorRef
    parameters: JsonObject = Field(default_factory=dict)
    rule: ProposalRuleV1
    rationale: str | None = Field(default=None, min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def selected_calculator_is_a_candidate(self) -> "EvaluationProposalItemV1":
        if self.selected_calculator not in self.candidates:
            raise ValueError("selected_calculator must appear in candidates")
        return self


class EvaluationProposalV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal_id: UUID
    revision: int = Field(ge=1)
    dataset_version_id: UUID
    profile_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    created_by: Literal["USER", "SYSTEM", "AGENT"]
    items: list[EvaluationProposalItemV1] = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def item_keys_are_unique(self) -> "EvaluationProposalV1":
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("proposal item keys must be unique")
        return self


class ProposalCandidateValidationV1(WireModel):
    calculator: ProposalCalculatorRef
    binding: MetricCalculatorBindingV1 | None = None
    applicability: ApplicabilityDecisionV1


class ProposalItemValidationV1(WireModel):
    key: str = Field(min_length=1)
    valid: bool
    selected_applicability: ApplicabilityDecisionV1
    candidates: list[ProposalCandidateValidationV1]
    findings: list[ApplicabilityFindingV1] = Field(default_factory=list)


class EvaluationProposalValidationV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal_id: UUID
    revision: int = Field(ge=1)
    valid: bool
    current_profile_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    items: list[ProposalItemValidationV1]
    findings: list[ApplicabilityFindingV1] = Field(default_factory=list)


class GuideCoverageCandidateV1(WireModel):
    calculator: ProposalCalculatorRef
    applicability: ApplicabilityDecisionV1
    deterministic: bool
    cost: Literal["LOW", "MEDIUM", "HIGH"]


class GuideCoverageItemV1(WireModel):
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    status: ApplicabilityStatus
    reason: str = Field(min_length=1)
    selected_calculator: ProposalCalculatorRef | None = None
    candidates: list[GuideCoverageCandidateV1] = Field(default_factory=list)


class EvaluationRecommendationV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal: EvaluationProposalV1 | None = None
    validation: EvaluationProposalValidationV1 | None = None
    guide_coverage: list[GuideCoverageItemV1]
    recommendations: list[str] = Field(default_factory=list)
    policy: Literal["DEMO_POLICY_V1"] = "DEMO_POLICY_V1"


__all__ = [
    "ApplicabilityDecisionV1",
    "ApplicabilityFindingV1",
    "EvaluationProposalV1",
    "EvaluationProposalValidationV1",
    "EvaluationRecommendationV1",
    "GuideCoverageItemV1",
    "ProposalCalculatorRef",
]

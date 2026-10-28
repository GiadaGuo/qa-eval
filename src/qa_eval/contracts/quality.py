from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.metrics import MetricSubject, VersionedKey

Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
ValueType = Literal["NUMBER", "STRING", "BOOLEAN", "OBJECT"]


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


class ContractApplicability(WireModel):
    modalities: list[Literal["text", "image", "audio", "video", "structured"]] = Field(
        default_factory=list
    )
    task_types: list[str] = Field(default_factory=list)


class ContractMetricSelector(VersionedKey):
    measurement: str = Field(min_length=1)


class ImplementationConstraints(WireModel):
    deterministic: bool | None = None
    attributes: JsonObject = Field(default_factory=dict)


class ContractCalculation(WireModel):
    parameters: JsonObject = Field(default_factory=dict)
    implementation_constraints: ImplementationConstraints = Field(
        default_factory=ImplementationConstraints
    )


class ThresholdRule(WireModel):
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NE"]
    threshold: Any

    @model_validator(mode="after")
    def threshold_matches_operator(self) -> "ThresholdRule":
        if self.operator in {"LT", "LTE", "GT", "GTE"} and (
            not isinstance(self.threshold, (int, float))
            or isinstance(self.threshold, bool)
        ):
            raise ValueError(f"{self.operator} requires a numeric threshold")
        return self


class QualityContractItem(WireModel):
    key: str = Field(min_length=1)
    metric: ContractMetricSelector
    scope: Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]
    calculation: ContractCalculation = Field(default_factory=ContractCalculation)
    rule: ThresholdRule
    severity: Severity
    on_missing: Literal["UNKNOWN", "SKIPPED"]


class ContractGate(WireModel):
    unknown_policy: Literal["PASS", "FAIL", "UNKNOWN"]
    fail_on_severities: list[Severity] = Field(min_length=1)


class QualityContractVersionV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    contract_id: UUID
    contract_version_id: UUID
    version_label: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: Literal["DRAFT", "PUBLISHED", "RETIRED"]
    applicability: ContractApplicability
    items: list[QualityContractItem] = Field(min_length=1)
    gate: ContractGate
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    published_at: datetime | None = None

    @field_validator("published_at")
    @classmethod
    def published_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_timezone(value, "published_at")

    @model_validator(mode="after")
    def validate_version(self) -> "QualityContractVersionV1":
        item_keys = [item.key for item in self.items]
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("contract item keys must be unique")
        if self.status == "PUBLISHED" and self.published_at is None:
            raise ValueError("PUBLISHED contract requires published_at")
        if self.status == "DRAFT" and self.published_at is not None:
            raise ValueError("DRAFT contract must not define published_at")
        return self


class RuleObservedValue(WireModel):
    value: Any
    value_type: ValueType
    unit: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def value_matches_type(self) -> "RuleObservedValue":
        valid = {
            "NUMBER": isinstance(self.value, (int, float))
            and not isinstance(self.value, bool),
            "STRING": isinstance(self.value, str),
            "BOOLEAN": isinstance(self.value, bool),
            "OBJECT": isinstance(self.value, (dict, list)),
        }[self.value_type]
        if not valid:
            raise ValueError("observed value does not match value_type")
        return self


class EvaluatedRule(WireModel):
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NE"]
    expected: Any


class RuleEvaluationV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    rule_evaluation_id: UUID
    run_id: UUID
    dataset_version_id: UUID
    contract_version_id: UUID
    contract_item_key: str = Field(min_length=1)
    metric_result_id: UUID
    metric: ContractMetricSelector
    subject: MetricSubject
    status: Literal["PASS", "FAIL", "UNKNOWN", "SKIPPED"]
    observed: RuleObservedValue | None = None
    rule: EvaluatedRule
    reason_code: Literal[
        "THRESHOLD_SATISFIED",
        "THRESHOLD_VIOLATED",
        "METRIC_MISSING",
        "METRIC_ERROR",
        "NOT_APPLICABLE",
    ]
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_has_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "evaluated_at")

    @model_validator(mode="after")
    def status_matches_observation_and_reason(self) -> "RuleEvaluationV1":
        if self.status in {"PASS", "FAIL"} and self.observed is None:
            raise ValueError(f"{self.status} requires observed")
        if self.status in {"UNKNOWN", "SKIPPED"} and self.observed is not None:
            raise ValueError(f"{self.status} must not contain observed")
        expected_reason = {
            "PASS": "THRESHOLD_SATISFIED",
            "FAIL": "THRESHOLD_VIOLATED",
        }.get(self.status)
        if expected_reason is not None and self.reason_code != expected_reason:
            raise ValueError(f"{self.status} requires {expected_reason}")
        return self


class IssueSource(WireModel):
    metric_result_id: UUID
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    measurement: str = Field(min_length=1)
    rule_evaluation_id: UUID | None = None


class DatasetIssueSubject(WireModel):
    type: Literal["DATASET"]
    dataset_version_id: UUID


class GroupIssueSubject(WireModel):
    type: Literal["GROUP"]
    group_id: str = Field(min_length=1)
    member_count: int = Field(ge=1)


class SampleIssueSubject(WireModel):
    type: Literal["SAMPLE"]
    sample_id: str = Field(min_length=1)


class PairIssueSubject(WireModel):
    type: Literal["PAIR"]
    pair_id: str = Field(min_length=1)
    left_sample_id: str = Field(min_length=1)
    right_sample_id: str = Field(min_length=1)


class ContentIssueSubject(WireModel):
    type: Literal["CONTENT"]
    sample_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)


class AnnotationIssueSubject(WireModel):
    type: Literal["ANNOTATION"]
    sample_id: str = Field(min_length=1)
    annotation_id: str = Field(min_length=1)


IssueSubject = Annotated[
    DatasetIssueSubject
    | GroupIssueSubject
    | SampleIssueSubject
    | PairIssueSubject
    | ContentIssueSubject
    | AnnotationIssueSubject,
    Field(discriminator="type"),
]


class RelatedIssueSubject(WireModel):
    relation: str = Field(min_length=1)
    subject: IssueSubject


class IssueEvidenceRef(WireModel):
    evidence_id: UUID
    locator: JsonObject


class QualityIssueV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    issue_id: UUID
    run_id: UUID
    dataset_version_id: UUID
    source: IssueSource
    detector: VersionedKey
    category: str = Field(min_length=1)
    code: str = Field(min_length=1)
    determination: Literal["CONFIRMED", "CANDIDATE"]
    severity: Severity
    scope: Literal["DATASET", "GROUP", "SAMPLE", "PAIR", "CONTENT", "ANNOTATION"]
    subject: IssueSubject
    related_subjects: list[RelatedIssueSubject] = Field(default_factory=list)
    message: str = Field(min_length=1)
    evidence: list[IssueEvidenceRef] = Field(min_length=1)
    dedup_key: str = Field(min_length=1)
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_have_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "issue timestamp")

    @model_validator(mode="after")
    def issue_is_consistent(self) -> "QualityIssueV1":
        if self.scope != self.subject.type:
            raise ValueError("scope must match subject.type")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class IssueIndexSummary(WireModel):
    confirmed_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)


class IssueIndexV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    issue_index_id: UUID
    run_id: UUID
    dataset_version_id: UUID
    artifact: ArtifactRef
    supported_filters: list[
        Literal[
            "metric_key",
            "code",
            "determination",
            "severity",
            "scope",
            "sample_id",
        ]
    ] = Field(min_length=1)
    summary: IssueIndexSummary

    @model_validator(mode="after")
    def artifact_matches_index(self) -> "IssueIndexV1":
        if self.artifact.kind != "ISSUE_INDEX":
            raise ValueError("IssueIndex artifact kind must be ISSUE_INDEX")
        expected = self.summary.confirmed_count + self.summary.candidate_count
        if self.artifact.record_count != expected:
            raise ValueError(
                "IssueIndex artifact record_count must equal summary total"
            )
        if len(self.supported_filters) != len(set(self.supported_filters)):
            raise ValueError("supported_filters must be unique")
        return self


class ReportCoverage(WireModel):
    evaluated: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    missing: int = Field(ge=0)
    error: int = Field(ge=0)
    not_evaluated: int = Field(ge=0)


class ReportDimension(WireModel):
    key: Literal["DOCUMENTATION", "DATA_QUALITY", "MODEL_APPLICATION"]
    status: Literal["FULL_COVERAGE", "PARTIAL_COVERAGE", "NOT_EVALUATED"]
    evaluated_metric_count: int = Field(ge=0)
    total_metric_count: int = Field(ge=0)

    @model_validator(mode="after")
    def evaluated_does_not_exceed_total(self) -> "ReportDimension":
        if self.evaluated_metric_count > self.total_metric_count:
            raise ValueError(
                "evaluated_metric_count must not exceed total_metric_count"
            )
        return self


class ReportMetricIdentity(VersionedKey):
    name: str = Field(min_length=1)


class ReportObservation(WireModel):
    key: str = Field(min_length=1)
    value: Any
    unit: str | None = Field(default=None, min_length=1)


class ReportRuleEvaluationRef(WireModel):
    rule_evaluation_id: UUID
    contract_item_key: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "UNKNOWN", "SKIPPED"]


class ReportMetricIssueSummary(IssueIndexSummary):
    by_code: dict[str, int] = Field(default_factory=dict)
    by_scope: dict[str, int] = Field(default_factory=dict)

    @field_validator("by_code", "by_scope")
    @classmethod
    def counts_are_nonnegative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("issue summary counts must be nonnegative")
        return value


class ReportMetricSection(WireModel):
    metric: ReportMetricIdentity
    metric_result_id: UUID
    status: Literal["OK", "NOT_APPLICABLE", "MISSING", "ERROR"]
    # 契约 item 绑定的主测量（metric.measurement 对应的 observation）；
    # 结果缺该观测（如 ERROR/MISSING）或结果不在契约内时为 None。
    measurement: ReportObservation | None = None
    # 同一次 Calculator 执行附带输出的诊断字段（不含主测量）。
    diagnostics: list[ReportObservation] = Field(default_factory=list)
    # 全量观测（主测量 + 诊断），保留以兼容既有消费者与历史报告。
    observations: list[ReportObservation] = Field(default_factory=list)
    rule_evaluations: list[ReportRuleEvaluationRef] = Field(default_factory=list)
    issue_summary: ReportMetricIssueSummary
    issue_index_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list)


class ReportIssueSummary(WireModel):
    confirmed_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    open_count: int = Field(ge=0)


class QualityReportV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: UUID
    run_id: UUID
    dataset_version_id: UUID
    contract_version_id: UUID
    generator: VersionedKey
    execution_status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "PARTIAL_SUCCEEDED",
        "FAILED",
        "CANCELED",
    ]
    gate_status: Literal["PASS", "FAIL", "UNKNOWN"]
    coverage: ReportCoverage
    dimensions: list[ReportDimension] = Field(min_length=1)
    metrics: list[ReportMetricSection] = Field(default_factory=list)
    issue_summary: ReportIssueSummary
    rendered_artifact_ids: list[UUID] = Field(default_factory=list)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "generated_at")

    @model_validator(mode="after")
    def report_sections_are_unique(self) -> "QualityReportV1":
        metric_keys = [(item.metric.key, item.metric.version) for item in self.metrics]
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("report metric sections must be unique by key and version")
        dimension_keys = [item.key for item in self.dimensions]
        if len(dimension_keys) != len(set(dimension_keys)):
            raise ValueError("report dimensions must be unique")
        return self

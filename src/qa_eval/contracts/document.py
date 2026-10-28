# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档质量评测报告契约（DocumentQualityReportV1）。
# 指标段语义对齐 MetricResultV1（metric/status/observations/evidence），
# 作为报告家族中的独立 report kind（ADR-0003）。
# ====================================================

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.metrics import MetricObservation, VersionedKey

ItemStatus = Literal[
    "SATISFIED", "INSUFFICIENT", "MISSING", "UNVERIFIABLE", "ERROR"
]
Fixability = Literal[
    "AUTO_FIXABLE", "DATA_DERIVABLE", "REQUIRES_HUMAN_INPUT"
]


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


class DocumentEvidence(WireModel):
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def range_is_ordered(self) -> "DocumentEvidence":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class DocumentItemSection(WireModel):
    item_code: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    status: ItemStatus
    document_evidence: list[DocumentEvidence] = Field(default_factory=list)
    inspected_scope: str = Field(min_length=1)
    expected_section: str | None = None
    reason: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    fixability: Fixability

    @model_validator(mode="after")
    def missing_has_expected_section(self) -> "DocumentItemSection":
        if self.status == "MISSING" and not (
            self.expected_section and self.expected_section.strip()
        ):
            raise ValueError(
                "MISSING items must provide expected_section"
            )
        return self


class DocumentMetricSection(WireModel):
    metric: VersionedKey
    metric_name: str = Field(min_length=1)
    status: Literal["OK", "ERROR"]
    # 字段语义对齐 MetricResultV1.observations；ERROR 指标无观测值
    observations: list[MetricObservation] = Field(default_factory=list)
    items: list[DocumentItemSection] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def error_metric_has_reason(self) -> "DocumentMetricSection":
        if self.status == "ERROR" and not (
            self.error and self.error.strip()
        ):
            raise ValueError("ERROR metrics must provide an error reason")
        if self.status == "OK":
            if not self.items:
                raise ValueError("OK metrics must contain item sections")
            if self.error is not None:
                raise ValueError("OK metrics must not carry an error")
        return self


class DocumentEvaluationStandard(WireModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentEvaluatorInfo(WireModel):
    version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    parameters: JsonObject = Field(default_factory=dict)
    structured_output_validation: bool = True


class DocumentOverallScore(WireModel):
    # 程序复算的等权平均；任一指标 ERROR 时为 null（D3）
    score: float | None = Field(default=None, ge=0, le=100)
    scoring_policy: str = Field(min_length=1)
    explanation: str | None = None


class DocumentMissingItem(WireModel):
    item_code: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    expected_section: str = Field(min_length=1)


class DocumentRecommendation(WireModel):
    item_code: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    fixability: Fixability


class DocumentSummary(WireModel):
    version: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_count: int = Field(ge=1)
    size_bytes: int = Field(ge=1)


class DocumentQualityReportV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: UUID
    task_id: UUID
    document_version_id: UUID
    dataset_id: UUID
    document: DocumentSummary
    evaluation_standard: DocumentEvaluationStandard
    evaluator: DocumentEvaluatorInfo
    execution_status: Literal[
        "SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED"
    ]
    metrics: list[DocumentMetricSection] = Field(min_length=1)
    overall: DocumentOverallScore
    missing_items: list[DocumentMissingItem] = Field(default_factory=list)
    recommendations: list[DocumentRecommendation] = Field(
        default_factory=list
    )
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(
        cls, value: datetime
    ) -> datetime:
        return _require_timezone(value, "generated_at")

    @model_validator(mode="after")
    def metric_sections_are_unique(self) -> "DocumentQualityReportV1":
        keys = [section.metric.key for section in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("metric sections must be unique by key")
        return self

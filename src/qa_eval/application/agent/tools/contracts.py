from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from qa_eval.application.services.quality_contract_service import (
    ContractGateRequest,
    QualityContractItemPublishRequest,
)
from qa_eval.contracts.proposals import EvaluationRecommendationV1


class GetDatasetProfileInput(BaseModel):
    dataset_version_id: UUID


class DatasetProfileToolResult(BaseModel):
    dataset_version_id: UUID
    profile_fingerprint: str
    profiler_key: str
    profiler_version: str
    profile: dict


class DiscoverMetricsInput(BaseModel):
    dataset_version_id: UUID
    dimension: Literal["DATA_QUALITY"] = "DATA_QUALITY"


class CalculatorSummary(BaseModel):
    key: str
    version: str
    availability: str
    deterministic: bool
    cost: str
    enabled: bool


class MetricDiscoveryItem(BaseModel):
    metric_key: str
    version: str
    name: str
    calculators: list[CalculatorSummary] = Field(default_factory=list)


class DiscoverMetricsToolResult(BaseModel):
    dataset_version_id: UUID
    dimension: Literal["DATA_QUALITY"]
    metrics: list[MetricDiscoveryItem]


class RecommendEvaluationProposalInput(BaseModel):
    dataset_version_id: UUID
    revision: int = Field(ge=1)


class RecommendEvaluationProposalToolResult(EvaluationRecommendationV1):
    pass


class CreateQualityContractInput(BaseModel):
    dataset_version_id: UUID
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=64)
    items: list[QualityContractItemPublishRequest] = Field(min_length=1)
    gate: ContractGateRequest = Field(default_factory=ContractGateRequest)


class CreateQualityContractToolResult(BaseModel):
    contract_version_id: UUID
    contract_id: UUID
    dataset_version_id: UUID
    item_count: int = Field(ge=1)
    status: Literal["PUBLISHED"]


class CreateEvaluationRunInput(BaseModel):
    contract_version_id: UUID


class EvaluationRunStatusInput(BaseModel):
    run_id: UUID


class EvaluationRunToolResult(BaseModel):
    run_id: UUID
    contract_version_id: UUID
    dataset_version_id: UUID
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: dict | None = None


class GetQualityReportInput(BaseModel):
    run_id: UUID


class QualityReportToolResult(BaseModel):
    run_id: UUID
    found: bool
    report_id: UUID | None = None
    execution_status: str | None = None
    gate_status: str | None = None
    evaluated_count: int | None = None
    error_count: int | None = None
    confirmed_issue_count: int | None = None

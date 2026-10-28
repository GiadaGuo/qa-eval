from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

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

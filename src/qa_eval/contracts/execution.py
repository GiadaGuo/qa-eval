from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.metrics import VersionedKey


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamp must include a timezone")
    return value


class PhysicalStageV1(WireModel):
    stage_id: str = Field(min_length=1)
    order: int = Field(ge=0)
    metric: VersionedKey
    calculator: JsonObject
    resolved_parameters: JsonObject
    input_schema_ref: str = Field(min_length=1)
    output_schema_ref: str = Field(min_length=1)


class PhysicalEvaluationPlanV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    physical_plan_id: UUID
    dataset_version_id: UUID
    contract_version_id: UUID
    executor_type: Literal["LOCAL"] = "LOCAL"
    stages: list[PhysicalStageV1] = Field(min_length=1)
    semantic_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime

    _validate_created_at = field_validator("created_at")(_aware)


RunStatus = Literal[
    "PENDING", "RUNNING", "PARTIAL_SUCCEEDED", "SUCCEEDED", "FAILED",
    "CANCEL_REQUESTED", "CANCELED",
]


class EvaluationRunV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    physical_plan_id: UUID
    dataset_version_id: UUID
    contract_version_id: UUID
    status: RunStatus
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: JsonObject | None = None

    _validate_created_at = field_validator("created_at")(_aware)
    _validate_started_at = field_validator("started_at")(_aware)
    _validate_finished_at = field_validator("finished_at")(_aware)


class StageRunV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    stage_run_id: UUID
    run_id: UUID
    physical_plan_id: UUID
    stage_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
    calculator: JsonObject
    resolved_parameters: JsonObject
    parameters_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_artifacts: list[JsonObject] = Field(default_factory=list)
    stats: JsonObject = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: JsonObject | None = None

    _validate_started_at = field_validator("started_at")(_aware)
    _validate_finished_at = field_validator("finished_at")(_aware)

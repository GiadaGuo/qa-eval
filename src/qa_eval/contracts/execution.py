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


class PlanningDiagnosticV1(WireModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    contract_item_key: str | None = None
    details: JsonObject = Field(default_factory=dict)


class LogicalInputDependencyV1(WireModel):
    kind: Literal[
        "DATASET_ARTIFACT", "CONTENT_ROLE", "DATASET_PROFILE", "OPERATION_OUTPUT"
    ]
    key: str = Field(min_length=1)
    required: bool = True


class LogicalCalculatorBindingV1(WireModel):
    calculator: VersionedKey
    deterministic: bool
    modalities: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    availability: str = Field(min_length=1)


class LogicalOutputBindingV1(WireModel):
    contract_item_key: str = Field(min_length=1)
    metric: VersionedKey
    measurement: str = Field(min_length=1)
    output_kind: Literal["METRIC_RESULT"] = "METRIC_RESULT"
    shared_computation_id: str | None = None


class LogicalOperationV1(WireModel):
    operation_id: str = Field(min_length=1)
    operation_type: Literal["METRIC", "SHARED_SEMANTIC", "AGGREGATE", "RULE"]
    metric: VersionedKey | None = None
    calculator_binding: LogicalCalculatorBindingV1 | None = None
    parameters: JsonObject = Field(default_factory=dict)
    input_dependencies: list[LogicalInputDependencyV1] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    output_bindings: list[LogicalOutputBindingV1] = Field(default_factory=list)
    sharing_constraints: JsonObject = Field(default_factory=dict)


class SharedComputationGroupV1(WireModel):
    shared_computation_id: str = Field(min_length=1)
    kind: Literal["LLM_SEMANTIC"] = "LLM_SEMANTIC"
    operation_id: str = Field(min_length=1)
    member_operation_ids: list[str] = Field(min_length=2)
    metric_keys: list[str] = Field(min_length=2)
    compatibility_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_identity: str = Field(min_length=1)


class LogicalEvaluationPlanV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    logical_plan_id: UUID
    plan_version: Literal["1.0"] = "1.0"
    dataset_version_id: UUID
    contract_id: UUID
    contract_version_id: UUID
    compiler: JsonObject
    operations: list[LogicalOperationV1] = Field(min_length=1)
    dependency_edges: list[tuple[str, str]] = Field(default_factory=list)
    shared_computation_groups: list[SharedComputationGroupV1] = Field(
        default_factory=list
    )
    output_bindings: list[LogicalOutputBindingV1] = Field(min_length=1)
    diagnostics: list[PlanningDiagnosticV1] = Field(default_factory=list)
    semantic_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime

    _validate_created_at = field_validator("created_at")(_aware)


class PhysicalStageV1(WireModel):
    stage_id: str = Field(min_length=1)
    order: int = Field(ge=0)
    metric: VersionedKey
    calculator: JsonObject
    resolved_parameters: JsonObject
    input_schema_ref: str = Field(min_length=1)
    output_schema_ref: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    logical_operation_ids: list[str] = Field(default_factory=list)
    stage_kind: Literal["DIRECT", "SHARED_INFERENCE"] = "DIRECT"
    shared_computation_id: str | None = None
    output_bindings: list[JsonObject] = Field(default_factory=list)


class PhysicalEvaluationPlanV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    physical_plan_id: UUID
    logical_plan_id: UUID | None = None
    dataset_version_id: UUID
    contract_version_id: UUID
    executor_type: Literal["LOCAL"] = "LOCAL"
    stages: list[PhysicalStageV1] = Field(min_length=1)
    semantic_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime

    _validate_created_at = field_validator("created_at")(_aware)


RunStatus = Literal[
    "PENDING",
    "RUNNING",
    "PARTIAL_SUCCEEDED",
    "SUCCEEDED",
    "FAILED",
    "CANCEL_REQUESTED",
    "CANCELED",
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

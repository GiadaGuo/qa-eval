from typing import Literal

from pydantic import Field, model_validator

from qa_eval.contracts.base import JsonObject, WireModel


class StandardReference(WireModel):
    standard: str = Field(min_length=1)
    code: str = Field(min_length=1)


class MetricApplicability(WireModel):
    modalities: list[Literal["text", "image", "audio", "video", "structured"]]
    required_roles: list[str] = Field(default_factory=list)


class MetricMeasurement(WireModel):
    key: str = Field(min_length=1)
    value_type: Literal["NUMBER", "STRING", "BOOLEAN", "OBJECT"]
    unit: str | None = Field(default=None, min_length=1)
    minimum: float | None = None
    maximum: float | None = None
    direction: Literal["LOWER_IS_BETTER", "HIGHER_IS_BETTER", "NEUTRAL"]
    interpretation: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> "MetricMeasurement":
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("measurement minimum must not exceed maximum")
        return self


class MetricDefinitionBody(WireModel):
    requirement: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class MetricDefinitionV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    metric_key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    dimension: Literal["DOCUMENTATION", "DATA_QUALITY", "MODEL_APPLICATION"]
    parent_metric_key: str | None = Field(default=None, min_length=1)
    standard_refs: list[StandardReference] = Field(min_length=1)
    supported_scopes: list[Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]] = (
        Field(min_length=1)
    )
    applicability: MetricApplicability
    measurements: list[MetricMeasurement] = Field(min_length=1)
    persistence_policy: Literal[
        "AGGREGATE_ONLY", "AGGREGATE_AND_EVIDENCE", "FULL_RESULTS"
    ]
    definition: MetricDefinitionBody

    @model_validator(mode="after")
    def keys_are_unique(self) -> "MetricDefinitionV1":
        keys = [item.key for item in self.measurements]
        if len(keys) != len(set(keys)):
            raise ValueError("measurement keys must be unique")
        if len(self.supported_scopes) != len(set(self.supported_scopes)):
            raise ValueError("supported_scopes must be unique")
        return self


class SupportedMetric(WireModel):
    metric_key: str = Field(min_length=1)
    metric_version_range: str = Field(min_length=1)


class CalculatorInputContract(WireModel):
    sample_record_schema: str = Field(min_length=1)
    # Empty for calculators whose selector is supplied as a validated parameter
    # (for example a metadata field or content role chosen by the user).
    role_selectors: list[str] = Field(default_factory=list)


class CalculatorCapabilities(WireModel):
    modalities: list[Literal["text", "image", "audio", "video", "structured"]]
    scopes: list[Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]]
    produces_evidence: bool


class CalculatorRuntime(WireModel):
    kind: Literal["PYTHON_IN_PROCESS"] = "PYTHON_IN_PROCESS"
    python_requires: str = Field(default=">=3.12", min_length=1)
    dependencies: list[str] = Field(default_factory=list)


class CalculatorResourceProfile(WireModel):
    cost: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    memory_mb: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=300, ge=1)


class CalculatorAvailability(WireModel):
    status: Literal["AVAILABLE", "UNAVAILABLE", "DEPRECATED"] = "AVAILABLE"
    reason: str | None = Field(default=None, min_length=1)


class SensitiveContentPolicy(WireModel):
    access: Literal["NONE", "NORMALIZED_CONTENT", "RAW_CONTENT"] = "NORMALIZED_CONTENT"
    emits_raw_content: bool = False


class InferenceInputRole(WireModel):
    alias: str = Field(min_length=1)
    role: str = Field(min_length=1)
    required: bool = True


class InferenceExecutionCapability(WireModel):
    """Execution metadata for calculators that score shared model output."""

    mode: Literal["DIRECT", "SHARED_INFERENCE"] = "DIRECT"
    operator_type: str | None = Field(default=None, min_length=1)
    prompt_family: str | None = Field(default=None, min_length=1)
    prompt_version: str | None = Field(default=None, min_length=1)
    rubric: JsonObject | None = None
    rubric_version: str | None = Field(default=None, min_length=1)
    rubric_output_schema: JsonObject | None = None
    output_field: str | None = Field(default=None, min_length=1)
    aggregation_strategy: JsonObject | None = None
    input_roles: list[InferenceInputRole] = Field(default_factory=list)
    static_context: JsonObject = Field(default_factory=dict)
    required_static_context: list[str] = Field(default_factory=list)
    shareability_constraints: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def shared_inference_is_fully_declared(self) -> "InferenceExecutionCapability":
        if self.mode == "SHARED_INFERENCE" and not all(
            (
                self.operator_type,
                self.prompt_family,
                self.prompt_version,
                self.rubric,
                self.rubric_version,
                self.rubric_output_schema,
                self.output_field,
                self.aggregation_strategy,
            )
        ):
            raise ValueError(
                "SHARED_INFERENCE execution requires operator_type, prompt_family, "
                "prompt_version, rubric, rubric_version, rubric_output_schema, "
                "output_field, and aggregation_strategy"
            )
        aliases = [item.alias for item in self.input_roles]
        if len(aliases) != len(set(aliases)):
            raise ValueError("inference input role aliases must be unique")
        if len(self.required_static_context) != len(
            set(self.required_static_context)
        ):
            raise ValueError("required_static_context keys must be unique")
        return self


class CalculatorDescriptorV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    calculator_key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # None marks a registered-but-unimplemented placeholder whose execution is
    # blocked by availability; TrustedCalculatorLoader never resolves it.
    entrypoint: str | None = Field(default=None, min_length=1)
    supported_metrics: list[SupportedMetric] = Field(min_length=1)
    input_contract: CalculatorInputContract
    parameter_schema_ref: str = Field(min_length=1)
    parameter_schema: JsonObject
    output_schema_ref: str = Field(min_length=1)
    deterministic: bool
    capabilities: CalculatorCapabilities
    # None for placeholders without implementation code (see entrypoint).
    code_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    runtime: CalculatorRuntime = Field(default_factory=CalculatorRuntime)
    input_artifact_requirements: list[str] = Field(
        default_factory=lambda: ["NORMALIZED_DATA"]
    )
    resource_profile: CalculatorResourceProfile = Field(
        default_factory=CalculatorResourceProfile
    )
    applicability_constraints: JsonObject = Field(default_factory=dict)
    semantic_coverage: JsonObject = Field(default_factory=dict)
    availability: CalculatorAvailability = Field(default_factory=CalculatorAvailability)
    sensitive_content_policy: SensitiveContentPolicy = Field(
        default_factory=SensitiveContentPolicy
    )
    execution: InferenceExecutionCapability = Field(
        default_factory=InferenceExecutionCapability
    )

    @model_validator(mode="after")
    def executable_calculator_declares_runtime_identity(
        self,
    ) -> "CalculatorDescriptorV1":
        if self.availability.status == "AVAILABLE" and (
            self.entrypoint is None or self.code_digest is None
        ):
            raise ValueError(
                "AVAILABLE calculators must declare an entrypoint and code_digest"
            )
        return self


class MetricCalculatorBindingV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    calculator_key: str = Field(min_length=1)
    calculator_version: str = Field(min_length=1)
    priority: int = Field(ge=0)
    enabled: bool
    # Empty preserves legacy bindings, which predate explicit output contracts.
    # New bindings should enumerate every measurement the Calculator may emit.
    produced_measurements: list[str] = Field(default_factory=list)
    compatibility: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def produced_measurement_keys_are_unique(self) -> "MetricCalculatorBindingV1":
        if len(self.produced_measurements) != len(set(self.produced_measurements)):
            raise ValueError("produced_measurements must be unique")
        if any(not key for key in self.produced_measurements):
            raise ValueError("produced_measurements must not contain empty keys")
        return self

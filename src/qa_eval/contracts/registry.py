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
    role_selectors: list[str] = Field(min_length=1)


class CalculatorCapabilities(WireModel):
    modalities: list[Literal["text", "image", "audio", "video", "structured"]]
    scopes: list[Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]]
    produces_evidence: bool


class CalculatorDescriptorV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    calculator_key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    supported_metrics: list[SupportedMetric] = Field(min_length=1)
    input_contract: CalculatorInputContract
    parameter_schema_ref: str = Field(min_length=1)
    parameter_schema: JsonObject
    output_schema_ref: str = Field(min_length=1)
    deterministic: bool
    capabilities: CalculatorCapabilities
    code_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MetricCalculatorBindingV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    calculator_key: str = Field(min_length=1)
    calculator_version: str = Field(min_length=1)
    priority: int = Field(ge=0)
    enabled: bool
    compatibility: JsonObject = Field(default_factory=dict)

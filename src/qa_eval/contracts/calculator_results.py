from math import isclose
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.metrics import EvidenceArtifact, VersionedKey

Count = Annotated[StrictInt, Field(ge=0)]


class StatisticDefinition(WireModel):
    key: str = Field(min_length=1)
    kind: Literal["count", "ratio", "scalar", "distribution", "object"]
    unit: str = Field(min_length=1)
    population: str = Field(min_length=1)
    method: str = Field(min_length=1)
    minimum: float | None = None
    maximum: float | None = None
    numerator_key: str | None = None
    denominator_key: str | None = None
    partition_of: str | None = None


class CalculatorOutputSchemaV1(WireModel):
    schema_version: Literal["1.0"]
    statistics: list[StatisticDefinition] = Field(min_length=1)
    decision_retention: Literal["NONE", "ALL", "EXCEPTIONS", "SAMPLE"] = "NONE"
    decision_population_key: str | None = None

    @model_validator(mode="after")
    def validate_definitions(self):
        kinds = {item.key: item.kind for item in self.statistics}
        if len(kinds) != len(self.statistics):
            raise ValueError("duplicate statistic keys")
        for item in self.statistics:
            if (
                item.minimum is not None
                and item.maximum is not None
                and item.minimum > item.maximum
            ):
                raise ValueError("statistic minimum exceeds maximum")
            refs = (item.numerator_key, item.denominator_key)
            if item.kind == "ratio":
                if any(kinds.get(key) != "count" for key in refs):
                    raise ValueError(
                        "ratios require numerator and denominator count keys"
                    )
            elif any(key is not None for key in refs):
                raise ValueError("only ratios may reference numerator and denominator")
            if item.partition_of is not None and (
                item.kind != "count"
                or kinds.get(item.partition_of) != "count"
                or item.partition_of == item.key
            ):
                raise ValueError("partitions require distinct count keys")
        if (
            self.decision_retention != "NONE"
            and kinds.get(self.decision_population_key) != "count"
        ):
            raise ValueError("decision retention requires a count population")
        return self

    def validate_statistics(self, values: dict) -> None:
        if values.keys() != {item.key for item in self.statistics}:
            raise ValueError("statistics do not match declared keys")
        partitions: dict[str, int] = {}
        for item in self.statistics:
            value = values[item.key]
            if item.kind == "count":
                if type(value) is not int or value < 0:
                    raise ValueError(f"{item.key} requires a nonnegative integer")
            elif item.kind in {"distribution", "object"}:
                if not isinstance(value, dict) or (
                    item.kind == "distribution"
                    and any(
                        type(count) is not int or count < 0 for count in value.values()
                    )
                ):
                    raise ValueError(f"invalid {item.kind}: {item.key}")
                continue
            elif item.kind == "ratio":
                numerator, denominator = (
                    values[item.numerator_key],
                    values[item.denominator_key],
                )
                if (
                    type(numerator) is not int
                    or type(denominator) is not int
                    or not 0 <= numerator <= denominator
                ):
                    raise ValueError(f"invalid ratio population: {item.key}")
                if denominator == 0 and value is None:
                    continue
                if (
                    not denominator
                    or type(value) not in (int, float)
                    or not isclose(value, numerator / denominator)
                ):
                    raise ValueError(f"invalid ratio: {item.key}")
            if type(value) not in (int, float) or not -float("inf") < value < float(
                "inf"
            ):
                raise ValueError(f"invalid numeric value: {item.key}")
            if (item.minimum is not None and value < item.minimum) or (
                item.maximum is not None and value > item.maximum
            ):
                raise ValueError(f"statistic outside declared range: {item.key}")
            if item.partition_of:
                partitions[item.partition_of] = (
                    partitions.get(item.partition_of, 0) + value
                )
        if any(values[key] != total for key, total in partitions.items()):
            raise ValueError("statistic population does not close")


class ObservationLocationV1(WireModel):
    content_id: str = Field(min_length=1)
    coordinate_space: str = Field(min_length=1)
    start: Count
    end: Count
    attributes: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("evidence span must be nonempty")
        return self


class DecisionEvidenceLocatorV1(WireModel):
    artifact_key: str = Field(min_length=1)
    row_id: str = Field(min_length=1)


class CalculatorDecisionV1(WireModel):
    row_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    target: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    locations: list[ObservationLocationV1] = Field(default_factory=list)
    evidence_refs: list[DecisionEvidenceLocatorV1] = Field(default_factory=list)
    attributes: JsonObject = Field(default_factory=dict)


DECISION_SCHEMA_REF = "qa-eval://schemas/calculator-decision/1.0.schema.json"


class CalculatorResultV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    calculator_result_id: UUID
    run_id: UUID
    stage_run_id: UUID
    dataset_version_id: UUID
    contract_item_key: str
    output_slot_id: str
    metric: VersionedKey
    calculator_key: str
    calculator_version: str
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["OK", "PARTIAL", "NOT_APPLICABLE", "MISSING", "ERROR"]
    status_detail: JsonObject = Field(default_factory=dict)
    statistics: JsonObject = Field(default_factory=dict)
    output_schema: CalculatorOutputSchemaV1
    decision_artifact_refs: list[EvidenceArtifact] = Field(default_factory=list)
    evidence_artifact_refs: list[EvidenceArtifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self):
        if self.status in {"OK", "PARTIAL"} or self.statistics:
            self.output_schema.validate_statistics(self.statistics)
        if self.status == "ERROR" and (
            self.statistics
            or self.decision_artifact_refs
            or self.evidence_artifact_refs
        ):
            raise ValueError("failed results cannot publish observations or artifacts")
        return self

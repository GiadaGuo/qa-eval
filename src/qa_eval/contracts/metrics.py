from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.base import JsonObject, TimestampedWireModel, WireModel


class VersionedKey(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)


class MetricSubject(WireModel):
    scope: Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]
    id: str = Field(min_length=1)


class MetricObservation(WireModel):
    key: str = Field(min_length=1)
    value: Any
    value_type: Literal["NUMBER", "STRING", "BOOLEAN", "OBJECT"]
    unit: str | None = Field(default=None, min_length=1)
    numerator: int | float | None = None
    denominator: int | float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def value_matches_declared_type(self) -> "MetricObservation":
        valid = {
            "NUMBER": isinstance(self.value, (int, float))
            and not isinstance(self.value, bool),
            "STRING": isinstance(self.value, str),
            "BOOLEAN": isinstance(self.value, bool),
            "OBJECT": isinstance(self.value, (dict, list)),
        }[self.value_type]
        if not valid:
            raise ValueError("observation value does not match value_type")
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("numerator and denominator must be provided together")
        return self


class PopulationExclusion(WireModel):
    reason: str = Field(min_length=1)
    count: int = Field(ge=0)


class MetricPopulation(WireModel):
    input_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    exclusions: list[PopulationExclusion] = Field(default_factory=list)

    @model_validator(mode="after")
    def counts_close(self) -> "MetricPopulation":
        if self.evaluated_count + self.excluded_count != self.input_count:
            raise ValueError("evaluated_count + excluded_count must equal input_count")
        if sum(item.count for item in self.exclusions) != self.excluded_count:
            raise ValueError("exclusion counts must equal excluded_count")
        return self


class SemanticCoverage(WireModel):
    level: Literal["FULL", "PARTIAL"]
    covered: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EvidenceSummary(WireModel):
    record_count: int = Field(ge=0)
    error_count: int = Field(default=0, ge=0)
    attributes: JsonObject = Field(default_factory=dict)


class EvidenceRedaction(WireModel):
    contains_raw_content: bool
    policy: str = Field(min_length=1)


class EvidenceArtifact(WireModel):
    evidence_id: UUID
    kind: str = Field(min_length=1)
    schema_ref: str = Field(min_length=1)
    artifact: ArtifactRef
    subject_scope: Literal["DATASET", "SAMPLE", "CONTENT", "ANNOTATION"]
    summary: EvidenceSummary
    redaction: EvidenceRedaction


class MetricResultV1(TimestampedWireModel):
    schema_version: Literal["1.0"] = "1.0"
    metric_result_id: UUID
    run_id: UUID
    stage_run_id: UUID
    dataset_version_id: UUID
    metric: VersionedKey
    subject: MetricSubject
    status: Literal["OK", "PARTIAL", "NOT_APPLICABLE", "MISSING", "ERROR"]
    status_detail: JsonObject = Field(default_factory=dict)
    observations: list[MetricObservation] = Field(default_factory=list)
    population: MetricPopulation | None = None
    semantic_coverage: SemanticCoverage | None = None
    evidence: list[EvidenceArtifact] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def observations_match_status(self) -> "MetricResultV1":
        if self.status in {"OK", "PARTIAL"} and not self.observations:
            raise ValueError(f"{self.status} result requires at least one observation")
        if self.status not in {"OK", "PARTIAL"} and self.observations:
            raise ValueError(f"{self.status} result must not contain observations")
        if self.status == "PARTIAL" and (
            self.semantic_coverage is None
            or self.semantic_coverage.level != "PARTIAL"
        ):
            raise ValueError("PARTIAL result requires PARTIAL semantic coverage")
        keys = [item.key for item in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("observation keys must be unique")
        return self

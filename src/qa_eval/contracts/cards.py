from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.base import JsonObject, TimestampedWireModel, WireModel
from qa_eval.contracts.datasets import (
    DatasetProfileSummary,
    ProfileIdentity,
    ProfilerIdentity,
    ProfileStatistics,
    ProfileStructure,
    ProfilingMetadata,
)


class DatasetCardDeclarationsV1(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    basic_information: JsonObject = Field(default_factory=dict)
    content_characteristics: JsonObject = Field(default_factory=dict)
    construction_process: JsonObject = Field(default_factory=dict)
    application_description: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def fields_are_from_the_frozen_schema(self) -> DatasetCardDeclarationsV1:
        allowed = {
            "basic_information": {
                "name",
                "scale",
                "format",
                "file_structure",
                "access",
                "support",
            },
            "content_characteristics": {
                "modality",
                "distribution",
                "labels",
                "example",
                "limitations",
            },
            "construction_process": {
                "source",
                "collection",
                "processing",
                "annotation",
                "version_record",
            },
            "application_description": {
                "license",
                "scenario",
                "evaluation",
                "benchmark",
                "case",
            },
        }
        for group, permitted in allowed.items():
            unknown = set(getattr(self, group)) - permitted
            if unknown:
                raise ValueError(
                    f"Unknown {group} declaration fields: {sorted(unknown)}"
                )
        return self


class DatasetCardRevisionV1(TimestampedWireModel):
    schema_version: Literal["1.0"] = "1.0"
    card_id: UUID
    dataset_version_id: UUID
    revision: int = Field(ge=1)
    lifecycle_status: Literal["DRAFT", "CONFIRMED", "PUBLISHED", "SUPERSEDED"]
    document: DatasetCardDeclarationsV1
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DatasetProfileFieldCandidateV1(TimestampedWireModel):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: UUID
    dataset_version_id: UUID
    card_revision: int | None = Field(default=None, ge=1)
    field_path: str = Field(min_length=1)
    value: Any
    value_role: Literal["DECLARED", "OBSERVED"]
    source_type: Literal["MANUAL", "DOCUMENT", "SYSTEM", "LLM"]
    source_ref: JsonObject = Field(default_factory=dict)
    evidence_locator: JsonObject = Field(default_factory=dict)
    extraction_rule_version: str | None = None
    review_status: Literal[
        "PENDING_REVIEW", "CONFIRMED", "REJECTED", "SUPERSEDED"
    ]
    supersedes_id: UUID | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DatasetProfileCardRefV1(WireModel):
    card_id: UUID
    revision: int = Field(ge=1)
    lifecycle_status: Literal["PUBLISHED"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetProfileObservationsV1_1(WireModel):
    profile_id: UUID
    profiler: ProfilerIdentity
    status: Literal["OK", "ERROR"]
    summary: DatasetProfileSummary
    identity: ProfileIdentity | None = None
    structure: ProfileStructure | None = None
    statistics: ProfileStatistics | None = None
    profiling_metadata: ProfilingMetadata | None = None
    field_profiles: list[JsonObject] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    created_at: datetime


class DatasetProfileProvenanceV1_1(WireModel):
    language: JsonObject = Field(default_factory=dict)
    application_context_source: Literal[
        "migrated-to-declarations", "not-present"
    ] = "not-present"


class DatasetProfileV1_1(TimestampedWireModel):
    schema_version: Literal["1.1"] = "1.1"
    profile_id: UUID
    dataset_version_id: UUID
    card: DatasetProfileCardRefV1 | None = None
    declarations: DatasetCardDeclarationsV1
    observations: DatasetProfileObservationsV1_1
    provenance: DatasetProfileProvenanceV1_1
    created_at: datetime


class DatasetCardUpdateRequest(WireModel):
    expected_revision: int = Field(ge=1)
    document: DatasetCardDeclarationsV1


class CandidateCreateRequest(WireModel):
    field_path: str = Field(min_length=1)
    value: Any
    value_role: Literal["DECLARED", "OBSERVED"] = "DECLARED"
    source_type: Literal["MANUAL", "DOCUMENT", "SYSTEM", "LLM"] = "MANUAL"
    source_ref: JsonObject = Field(default_factory=dict)
    evidence_locator: JsonObject = Field(default_factory=dict)
    extraction_rule_version: str | None = None


class CandidateReviewRequest(WireModel):
    decision: Literal["CONFIRMED", "REJECTED"]
    reviewed_by: str = Field(min_length=1)

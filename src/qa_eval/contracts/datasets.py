from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from qa_eval.contracts.artifacts import ArtifactRef, Checksum
from qa_eval.contracts.base import JsonObject, TimestampedWireModel, WireModel

Modality = Literal["text", "image", "audio", "video", "structured"]


class AdapterIdentity(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)


class SourceSnapshot(WireModel):
    source_type: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    captured_at: datetime
    consistency: str = Field(min_length=1)
    record_count: int = Field(ge=0)
    details: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def captured_at_has_timezone(self) -> "SourceSnapshot":
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return self


class MappingTarget(WireModel):
    kind: Literal[
        "SAMPLE_ID",
        "CONTENT",
        "ANNOTATION",
        "METADATA",
        "SOURCE_ATTRIBUTE",
        "TEMPORAL",
    ]
    name: str | None = Field(default=None, min_length=1)
    role: str | None = Field(default=None, min_length=1)
    modality: Modality | None = None


class FieldMappingRule(WireModel):
    source_path: str = Field(min_length=1)
    action: Literal["MAP", "PASSTHROUGH", "IGNORE"]
    target: MappingTarget | None = None
    value_type: str | None = Field(default=None, min_length=1)
    presence_policy: Literal["PRESERVE", "DROP_NULL", "REJECT_MISSING"] = "PRESERVE"
    required_for_normalization: bool = False

    @model_validator(mode="after")
    def target_matches_action(self) -> "FieldMappingRule":
        if self.action == "MAP" and self.target is None:
            raise ValueError("MAP requires target")
        if self.action != "MAP" and self.target is not None:
            raise ValueError(f"{self.action} must not define target")
        return self


class FieldMappingSpecV2(WireModel):
    schema_version: Literal["2.0"] = "2.0"
    adapter: AdapterIdentity
    mappings: list[FieldMappingRule]


class DeclaredContext(WireModel):
    languages: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    attributes: JsonObject = Field(default_factory=dict)


class DatasetManifestV2(TimestampedWireModel):
    schema_version: Literal["2.0"] = "2.0"
    dataset_id: UUID
    dataset_version_id: UUID
    name: str = Field(min_length=1)
    modalities: list[Modality] = Field(min_length=1)
    source_snapshot: SourceSnapshot
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    field_mapping: FieldMappingSpecV2
    declared_context: DeclaredContext = Field(default_factory=DeclaredContext)
    governance: JsonObject = Field(default_factory=dict)
    created_at: datetime


class InlineTextPayload(WireModel):
    type: Literal["inline_text"]
    text: str
    format: Literal["plain", "html", "markdown", "code"]
    language: str | None = Field(default=None, min_length=1)
    encoding: str | None = Field(default=None, min_length=1)


class AssetPayload(WireModel):
    type: Literal["asset"]
    uri: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    checksum: Checksum
    size_bytes: int = Field(ge=0)
    technical_metadata: JsonObject = Field(default_factory=dict)


class StructuredPayload(WireModel):
    type: Literal["structured"]
    data: Any
    schema_ref: str = Field(min_length=1)


ContentPayload = Annotated[
    InlineTextPayload | AssetPayload | StructuredPayload,
    Field(discriminator="type"),
]


class ContentItem(WireModel):
    content_id: str = Field(min_length=1)
    modality: Modality
    role: str = Field(min_length=1)
    presence: Literal["PRESENT", "NULL", "MISSING"]
    payload: ContentPayload | None = None
    attributes: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def payload_matches_presence(self) -> "ContentItem":
        payload_was_provided = "payload" in self.model_fields_set
        if self.presence == "PRESENT" and self.payload is None:
            raise ValueError("PRESENT content requires payload")
        if self.presence != "PRESENT" and payload_was_provided:
            raise ValueError("NULL or MISSING content must omit payload")
        if self.modality != "text" and isinstance(self.payload, InlineTextPayload):
            raise ValueError("inline_text payload requires text modality")
        return self


class AnnotationTarget(WireModel):
    content_id: str = Field(min_length=1)


class AnnotationTask(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)


class AnnotationProvenance(WireModel):
    method: Literal["HUMAN", "MODEL", "RULE", "IMPORTED"]
    annotator_ids: list[str] = Field(default_factory=list)
    tool: str | None = None
    created_at: datetime | None = None


class Annotation(WireModel):
    annotation_id: str = Field(min_length=1)
    task: AnnotationTask
    targets: list[AnnotationTarget] = Field(min_length=1)
    value: JsonObject
    provenance: AnnotationProvenance
    status: Literal["DRAFT", "FINAL", "REJECTED"]
    attributes: JsonObject = Field(default_factory=dict)


class SampleSource(WireModel):
    source_artifact_id: UUID
    source_record_id: str | None = None
    source_row: int | None = Field(default=None, ge=0)
    locator: str | None = None
    attributes: JsonObject = Field(default_factory=dict)


class TransformRef(WireModel):
    key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    config_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SampleLineage(WireModel):
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    transforms: list[TransformRef] = Field(default_factory=list)


class SampleRecordV2(WireModel):
    schema_version: Literal["2.0"] = "2.0"
    dataset_version_id: UUID
    sample_id: str = Field(min_length=1)
    contents: list[ContentItem] = Field(min_length=1)
    annotations: list[Annotation] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    source: SampleSource
    lineage: SampleLineage

    @model_validator(mode="after")
    def content_ids_are_unique(self) -> "SampleRecordV2":
        content_ids = [item.content_id for item in self.contents]
        if len(content_ids) != len(set(content_ids)):
            raise ValueError("content_id must be unique within a sample")
        known_ids = set(content_ids)
        referenced_ids = {
            target.content_id
            for annotation in self.annotations
            for target in annotation.targets
        }
        if unknown := referenced_ids - known_ids:
            raise ValueError(
                f"annotation targets unknown content IDs: {sorted(unknown)}"
            )
        return self


class RejectedSource(WireModel):
    source_artifact_id: UUID
    source_row: int | None = Field(default=None, ge=0)
    source_record_id: str | None = None


class RejectionError(WireModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    json_pointer: str | None = None
    retryable: bool


class RejectedRecordV1(TimestampedWireModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_version_id: UUID
    normalization_run_id: UUID
    source: RejectedSource
    error: RejectionError
    redacted_excerpt: JsonObject | str | None = None
    created_at: datetime

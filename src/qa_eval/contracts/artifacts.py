from typing import Literal
from uuid import UUID

from pydantic import Field

from qa_eval.contracts.base import WireModel


class Checksum(WireModel):
    algorithm: Literal["sha256"]
    value: str = Field(pattern=r"^[0-9a-f]{64}$")


ArtifactKind = Literal[
    "RAW_DATA",
    "DATASET_MANIFEST",
    "NORMALIZED_DATA",
    "REJECTED_RECORDS",
    "PROFILE",
    "STAGE_OUTPUT",
    "METRIC_EVIDENCE",
    "ISSUE_INDEX",
    "REPORT",
]


class ArtifactRef(WireModel):
    artifact_id: UUID
    kind: ArtifactKind
    uri: str = Field(min_length=1)
    format: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    schema_ref: str | None = Field(default=None, min_length=1)
    size_bytes: int = Field(ge=0)
    record_count: int | None = Field(default=None, ge=0)
    checksum: Checksum

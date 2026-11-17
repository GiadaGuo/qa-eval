from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from qa_eval.contracts.base import JsonObject, TimestampedWireModel, WireModel


class SnapshotArtifactIdentity(WireModel):
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProfileSnapshotV1(TimestampedWireModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: UUID
    dataset_version_id: UUID
    dataset_version: JsonObject
    card: JsonObject | None = None
    observed_profile: JsonObject
    manifest: SnapshotArtifactIdentity
    rules: list[JsonObject] = Field(default_factory=list)
    calculator_bindings: list[JsonObject] = Field(default_factory=list)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime


class ProfileSnapshotRefV1(WireModel):
    snapshot_id: UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact: SnapshotArtifactIdentity

"""Resolve an original-text range against a verified v3 record."""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from qa_eval.application.normalization.record_reader import RawSampleRecord


class RawTextLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_tokens: tuple[str, ...] = Field(min_length=1)
    artifact_sha256: str
    text_sha256: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    def verified_text(self, record: RawSampleRecord) -> str:
        if record.meta.artifact_sha256 != self.artifact_sha256:
            raise ValueError("evidence artifact checksum mismatch")
        value = record.data
        for token in self.field_tokens:
            if not isinstance(value, dict) or token not in value:
                raise ValueError("evidence field is unavailable")
            value = value[token]
        if not isinstance(value, str):
            raise TypeError("evidence field is not text")
        actual = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
        if actual != self.text_sha256:
            raise ValueError("evidence text checksum mismatch")
        if not self.start <= self.end <= len(value):
            raise ValueError("evidence range is outside original text")
        return value

    def excerpt(self, record: RawSampleRecord) -> str:
        return self.verified_text(record)[self.start : self.end]


class RawEvidenceLocation(BaseModel):
    """Common evidence location for exact v3 source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["3.0"] = "3.0"
    row_id: UUID
    sample_id: UUID
    content_id: str
    location: RawTextLocation
    masked_excerpt: str | None = None

    @staticmethod
    def identity(stage_id: UUID, sample_id: UUID, event_key: str) -> UUID:
        return uuid5(stage_id, f"{sample_id}:{event_key}")

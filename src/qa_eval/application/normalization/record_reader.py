"""Versioned readers for immutable JSONL record artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from qa_eval.contracts.datasets import SampleRecordV2

Presence = Literal["MISSING", "NULL", "EMPTY", "PRESENT"]


class RawRecordManifest(BaseModel):
    """The raw artifact is the canonical data representation for this version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version_id: UUID
    schema_version: Literal["3.0"] = "3.0"
    source_uri: str
    source_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reader: Literal["jsonl-utf8@1"] = "jsonl-utf8@1"


class RecordSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_uri: str
    artifact_sha256: str
    byte_start: int = Field(ge=0)
    byte_length: int = Field(gt=0)
    line_number: int = Field(gt=0)


class RawSampleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    dataset_version_id: UUID
    data: dict[str, Any]
    meta: RecordSource
    _raw_json: bytes = PrivateAttr()

    def response_json(self) -> bytes:
        envelope = self.model_dump_json(exclude={"data"}).encode("utf-8")
        return envelope[:-1] + b',"data":' + self._raw_json.strip() + b"}"

    def field(self, key: str) -> tuple[Presence, Any]:
        if key not in self.data:
            return "MISSING", None
        value = self.data[key]
        if value is None:
            return "NULL", None
        if isinstance(value, str) and value == "":
            return "EMPTY", value
        return "PRESENT", value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON object key")
        data[key] = value
    return data


def _invalid_constant(_: str) -> Any:
    raise ValueError("non-finite JSON number")


class RawJsonlRecordReader:
    def __init__(self, manifest: RawRecordManifest) -> None:
        self.manifest = manifest

    def decode(self, line: bytes, *, start: int, number: int) -> RawSampleRecord:
        data = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_object,
            parse_float=Decimal,
            parse_constant=_invalid_constant,
        )
        if not isinstance(data, dict):
            raise ValueError(f"JSONL record at line {number} must be an object")  # noqa: TRY004
        source = RecordSource(
            artifact_uri=self.manifest.source_uri,
            artifact_sha256=self.manifest.source_sha256,
            byte_start=start,
            byte_length=len(line),
            line_number=number,
        )
        record = RawSampleRecord(
            id=uuid5(
                self.manifest.dataset_version_id,
                f"{self.manifest.source_sha256}:{start}",
            ),
            dataset_version_id=self.manifest.dataset_version_id,
            data=data,
            meta=source,
        )
        record._raw_json = line
        return record

    def iter_records(self, stream: BinaryIO) -> Iterator[RawSampleRecord]:
        if stream.tell() != 0:
            raise ValueError("record stream must begin at byte zero")
        digest = hashlib.sha256()
        start = 0
        for number, line in enumerate(stream, 1):
            digest.update(line)
            if line.strip():
                yield self.decode(line, start=start, number=number)
            start += len(line)
        if f"sha256:{digest.hexdigest()}" != self.manifest.source_sha256:
            raise ValueError("record artifact checksum does not match Manifest")


def read_records(
    path: Path, *, manifest: RawRecordManifest | None = None
) -> Iterator[RawSampleRecord | SampleRecordV2]:
    """A v3 Manifest selects the raw reader. Legacy paths use the v2 contract."""

    with path.open("rb") as stream:
        if manifest is not None:
            yield from RawJsonlRecordReader(manifest).iter_records(stream)
            return
        for line in stream:
            if line.strip():
                yield SampleRecordV2.model_validate_json(line)

"""Immutable logical input descriptions for v3 runs."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Representation = Literal["json", "text/plain", "text/html"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FieldBinding(FrozenModel):
    semantic: str = Field(min_length=1)
    field_tokens: tuple[str, ...] = Field(min_length=1)
    representation: Representation
    source_representation: Representation | None = None
    transform: str | None = None


class MappingRevision(FrozenModel):
    dataset_version_id: UUID
    schema_fingerprint: str
    bindings: tuple[FieldBinding, ...]

    @model_validator(mode="after")
    def validate_bindings(self) -> MappingRevision:
        identities = [(item.semantic, item.representation) for item in self.bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("ambiguous semantic mapping")
        return self


class InputSlot(FrozenModel):
    name: str = Field(min_length=1)
    semantic: str = Field(min_length=1)
    representation: Representation
    value_type: Literal["string", "object", "array", "number", "boolean", "any"] = "any"
    cardinality: Literal["one", "many"] = "one"
    required: bool = True
    non_empty: bool = False


class CalculatorInputContract(FrozenModel):
    version: str
    grain: Literal["RECORD", "DATASET"]
    mode: Literal["DIRECT", "SHARED_INFERENCE"]
    slots: tuple[InputSlot, ...] = ()
    artifact_kinds: tuple[Literal["PROFILE_SNAPSHOT", "DATASET_MANIFEST"], ...] = ()

    @model_validator(mode="after")
    def validate_slots(self) -> CalculatorInputContract:
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError("duplicate logical input slot")
        return self

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


class SemanticDefinition(FrozenModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=128)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    type: Literal["string", "object", "array", "number", "boolean", "any"]
    representation: Representation
    cardinality: Literal["one", "many"] = "one"


class FieldBinding(FrozenModel):
    semantic: str = Field(min_length=1)
    field_tokens: tuple[str, ...] = Field(min_length=1)
    representation: Representation
    source_representation: Representation | None = None
    transform: str | None = None
    definition: SemanticDefinition | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    transform_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_frozen_binding(self) -> FieldBinding:
        if self.definition and (self.semantic, self.representation) != (
            self.definition.key, self.definition.representation,
        ):
            raise ValueError("frozen semantic definition does not match binding")
        if self.transform_digest and not self.transform:
            raise ValueError("a frozen transform digest requires a transform")
        return self


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


class MappingDraftItem(FrozenModel):
    binding: FieldBinding
    semantic_version: str | None = None
    confirmed: bool = False
    rationale: str | None = None


class InputSelector(FrozenModel):
    field_tokens: tuple[str, ...] | None = Field(default=None, min_length=1)
    semantic: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_choice(self):
        if (self.field_tokens is None) == (self.semantic is None):
            raise ValueError("select one raw path or semantic")
        return self


class InputSlot(FrozenModel):
    name: str = Field(min_length=1)
    semantic: str = Field(min_length=1)
    representation: Representation
    value_type: Literal["string", "object", "array", "number", "boolean", "any"] = "any"
    cardinality: Literal["one", "many"] = "one"
    required: bool = True
    non_empty: bool = False
    selectable: bool = Field(default=False, exclude_if=lambda value: not value)


class CalculatorInputContract(FrozenModel):
    version: str
    grain: Literal["RECORD", "DATASET", "DOCUMENT"]
    mode: Literal["DIRECT", "SHARED_INFERENCE"]
    slots: tuple[InputSlot, ...] = ()
    artifact_kinds: tuple[Literal["PROFILE_SNAPSHOT", "DATASET_MANIFEST", "DOCUMENT", "KENLM_MODEL"], ...] = ()

    @model_validator(mode="after")
    def validate_slots(self) -> CalculatorInputContract:
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError("duplicate logical input slot")
        return self

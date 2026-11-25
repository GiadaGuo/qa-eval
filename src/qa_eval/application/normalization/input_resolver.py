"""Resolve logical slots against frozen mappings and verified source rows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

from pydantic import Field

from qa_eval.application.normalization.record_reader import (
    RawRecordManifest,
    RawSampleRecord,
)
from qa_eval.contracts.logical_inputs import (
    CalculatorInputContract,
    FieldBinding,
    FrozenModel,
    InputSelector,
    InputSlot,
    MappingRevision,
    Representation,
)


@dataclass(frozen=True)
class Transform:
    key: str
    implementation_digest: str
    input_representation: Representation
    output_representation: Representation
    apply: Callable[[str], str]


class ArtifactInput(FrozenModel):
    kind: str
    uri: str
    sha256: str


class PlannedSlot(FrozenModel):
    slot: InputSlot
    binding: FieldBinding | None = None
    transform_digest: str | None = None


class FrozenInputPlan(FrozenModel):
    dataset_version_id: UUID
    manifest_fingerprint: str
    source_sha256: str
    revision_fingerprint: str
    contract_fingerprint: str
    slots: tuple[PlannedSlot, ...]
    artifacts: tuple[ArtifactInput, ...]
    selectors: dict[str, InputSelector] = Field(default_factory=dict, exclude_if=lambda value: not value)


@dataclass(frozen=True)
class ValueOrigin:
    source_sha256: str
    byte_start: int
    field_tokens: tuple[str, ...]
    transform: str | None
    transform_digest: str | None
    text_sha256: str | None


@dataclass(frozen=True)
class ResolvedValue:
    status: str
    value: Any = None
    origin: ValueOrigin | None = None


@dataclass(frozen=True)
class ResolvedRecord:
    sample_id: UUID
    values: Mapping[str, ResolvedValue]


_VALUE_TYPES = {
    "string": (str,),
    "object": (dict,),
    "array": (list,),
    "number": (int, Decimal),
    "boolean": (bool,),
}


def _text_digest(value: Any) -> str | None:
    if isinstance(value, str):
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return None


class InputResolver:
    def __init__(self, transforms: Iterable[Transform] = ()) -> None:
        self._cached_transform = lru_cache(maxsize=64)(self._apply_transform)
        self.transforms: dict[str, Transform] = {}
        for transform in transforms:
            if transform.key in self.transforms:
                raise ValueError(f"duplicate transform: {transform.key}")
            self.transforms[transform.key] = transform

    def _apply_transform(self, key: str, digest: str, value: str) -> str:
        transform = self.transforms[key]
        if transform.implementation_digest != digest:
            raise ValueError("frozen transform implementation is unavailable")
        return transform.apply(value)

    def preflight(
        self,
        manifest: RawRecordManifest,
        mapping: MappingRevision | None,
        contract: CalculatorInputContract,
        artifacts: Iterable[ArtifactInput] = (),
        *, selectors: Mapping[str, InputSelector] | None = None,
    ) -> FrozenInputPlan:
        if mapping is not None and (
            manifest.dataset_version_id != mapping.dataset_version_id
            or manifest.schema_fingerprint != mapping.schema_fingerprint
        ):
            raise ValueError("mapping does not match record Manifest")
        bindings = {
            (item.semantic, item.representation): item for item in (mapping.bindings if mapping else ())
        }
        selectors = selectors or {}
        if set(selectors) - {slot.name for slot in contract.slots if slot.selectable}:
            raise ValueError("selector supplied for a fixed or unknown slot")
        artifact_items = tuple(artifacts)
        artifact_map = {item.kind: item for item in artifact_items}
        if len(artifact_items) != len(artifact_map):
            raise ValueError("ambiguous independent artifact")
        if missing := set(contract.artifact_kinds) - artifact_map.keys():
            raise ValueError(f"missing independent artifact: {', '.join(sorted(missing))}")
        slots = []
        for slot in contract.slots:
            selector = selectors.get(slot.name)
            binding = None if slot.selectable else bindings.get((slot.semantic, slot.representation))
            if selector is not None:
                if selector.field_tokens is not None:
                    binding = FieldBinding(
                        semantic=slot.semantic, representation=slot.representation,
                        field_tokens=selector.field_tokens,
                    )
                else:
                    binding = bindings.get((selector.semantic, slot.representation))
            if binding is None and slot.required:
                raise ValueError(f"unbound required slot: {slot.name}")
            digest = None
            if binding is not None and binding.transform is not None:
                transform = self.transforms.get(binding.transform)
                if (
                    transform is None
                    or (binding.transform_digest is not None
                        and binding.transform_digest != transform.implementation_digest)
                    or transform.input_representation != binding.source_representation
                    or transform.output_representation != slot.representation
                ):
                    raise ValueError(f"incompatible transform for slot: {slot.name}")
                digest = transform.implementation_digest
            slots.append(PlannedSlot(slot=slot, binding=binding, transform_digest=digest))
        return FrozenInputPlan(
            dataset_version_id=manifest.dataset_version_id,
            manifest_fingerprint=FrozenManifest(manifest=manifest).fingerprint(),
            source_sha256=manifest.source_sha256,
            revision_fingerprint=mapping.fingerprint() if mapping else "",
            selectors=dict(selectors),
            contract_fingerprint=contract.fingerprint(),
            slots=tuple(slots),
            artifacts=tuple(artifact_map[kind] for kind in contract.artifact_kinds),
        )

    def iter_records(
        self, plan: FrozenInputPlan, records: Iterable[RawSampleRecord]
    ) -> Iterator[ResolvedRecord]:
        for planned in plan.slots:
            if planned.transform_digest is not None:
                assert planned.binding is not None
                transform = self.transforms.get(planned.binding.transform or "")
                if transform is None or transform.implementation_digest != planned.transform_digest:
                    raise ValueError("frozen transform implementation is unavailable")
        for record in records:
            if (
                record.dataset_version_id != plan.dataset_version_id
                or record.meta.artifact_sha256 != plan.source_sha256
            ):
                raise ValueError("record source does not match frozen plan")
            yield ResolvedRecord(
                record.id,
                {entry.slot.name: self._resolve(entry, record) for entry in plan.slots},
            )

    def _resolve(self, entry: PlannedSlot, record: RawSampleRecord) -> ResolvedValue:
        binding = entry.binding
        if binding is None:
            return ResolvedValue("UNBOUND")
        value: Any = record.data
        for token in binding.field_tokens:
            if isinstance(value, dict):
                if token not in value:
                    return ResolvedValue("MISSING")
                value = value[token]
            elif isinstance(value, list) and token.isascii() and token.isdecimal() and len(token) <= 9:
                if token != str(int(token)) or int(token) >= len(value):
                    return ResolvedValue("MISSING")
                value = value[int(token)]
            else:
                return ResolvedValue("INVALID_TYPE")
        if value is None:
            return ResolvedValue("NULL")
        values = value if entry.slot.cardinality == "many" else [value]
        if not isinstance(values, list):
            return ResolvedValue("INVALID_CARDINALITY")
        if binding.transform:
            if any(not isinstance(item, str) for item in values):
                return ResolvedValue("INVALID_TYPE")
            try:
                apply = self.transforms[binding.transform].apply
                values = [
                    self._cached_transform(binding.transform, entry.transform_digest, item)
                    if entry.transform_digest is not None and len(item) <= 8192
                    else apply(item)
                    for item in values
                ]
            except (ValueError, UnicodeError):
                return ResolvedValue("TRANSFORM_FAILED")
        types = _VALUE_TYPES.get(entry.slot.value_type)
        if types is not None and any(type(item) not in types for item in values):
            return ResolvedValue("INVALID_TYPE")
        result = values if entry.slot.cardinality == "many" else values[0]
        origin = ValueOrigin(
            record.meta.artifact_sha256, record.meta.byte_start, binding.field_tokens,
            binding.transform, entry.transform_digest, _text_digest(result),
        )
        if result == "" or (entry.slot.non_empty and not result):
            return ResolvedValue("EMPTY", result, origin)
        return ResolvedValue("PRESENT", result, origin)


class FrozenManifest(FrozenModel):
    manifest: RawRecordManifest

"""Internal schemas for policy structure parsing and evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import Field, model_validator

from qa_eval.contracts.base import WireModel

FlagStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNAVAILABLE"]
CoordinateStatus = Literal["EXACT", "BLOCK_ONLY", "UNAVAILABLE"]
StructureMode = Literal[
    "LEGAL_HIERARCHY", "OUTLINE_HIERARCHY", "GENERIC_DOCUMENT", "MIXED"
]


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int


@dataclass
class SourceLocation:
    normalized_text_start: int
    normalized_text_end: int
    source_html_start: int | None
    source_html_end: int | None
    source_html_spans: list[SourceSpan]
    dom_path: str | None
    coordinate_status: CoordinateStatus
    coordinate_space: str = "source_html_unicode_code_points"
    indexing: str = "zero_based_end_exclusive"
    reason: str | None = None


@dataclass
class StructureBlock:
    block_id: str
    text: str
    location: SourceLocation


@dataclass
class StructureNode:
    node_id: str
    node_type: str
    parent_id: str | None
    depth: int
    ordinal: int | None
    raw_number: str | None
    numbering_scope: str | None
    location: SourceLocation


@dataclass
class StructureParseResult:
    parser_key: str
    parser_version: str
    document_type: str
    document_type_source: str
    document_type_confidence: float
    mode: StructureMode
    mode_candidates: list[dict[str, Any]]
    normalized_text: str
    blocks: list[StructureBlock]
    nodes: list[StructureNode]
    unparsed_candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def public_dict(self, *, include_normalized_text: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_normalized_text:
            value.pop("normalized_text", None)
            for block in value["blocks"]:
                block.pop("text", None)
        return value


@dataclass
class FlagResult:
    status: FlagStatus
    reason_code: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


GROUND_TRUTH_TASK_KEY = "policy.structure.segmentation"
GROUND_TRUTH_TASK_VERSION = "1.0.0"
GROUND_TRUTH_SCHEMA_REF = "qa-eval://schemas/annotations/policy-structure/1.0.0"


class GroundTruthNodeV1(WireModel):
    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    parent_id: str | None = None
    normalized_text_start: int = Field(ge=0)
    normalized_text_end: int = Field(gt=0)

    @model_validator(mode="after")
    def range_is_ordered(self) -> GroundTruthNodeV1:
        if self.normalized_text_end <= self.normalized_text_start:
            raise ValueError("normalized_text_end must be greater than start")
        return self


class PolicyStructureGroundTruthV1(WireModel):
    schema_ref: Literal["qa-eval://schemas/annotations/policy-structure/1.0.0"] = (
        GROUND_TRUTH_SCHEMA_REF
    )
    nodes: list[GroundTruthNodeV1]

    @model_validator(mode="after")
    def node_ids_and_parents_are_valid(self) -> PolicyStructureGroundTruthV1:
        ids = [item.node_id for item in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("ground-truth node_id must be unique")
        known = set(ids)
        if unknown := {item.parent_id for item in self.nodes if item.parent_id} - known:
            raise ValueError(f"ground-truth parent IDs are unknown: {sorted(unknown)}")
        return self


__all__ = [
    "GROUND_TRUTH_SCHEMA_REF",
    "GROUND_TRUTH_TASK_KEY",
    "GROUND_TRUTH_TASK_VERSION",
    "CoordinateStatus",
    "FlagResult",
    "FlagStatus",
    "GroundTruthNodeV1",
    "PolicyStructureGroundTruthV1",
    "SourceLocation",
    "SourceSpan",
    "StructureBlock",
    "StructureMode",
    "StructureNode",
    "StructureParseResult",
]

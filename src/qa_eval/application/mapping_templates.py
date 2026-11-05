from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from qa_eval.contracts.datasets import FieldMappingSpecV2


class TemplateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_fields: list[str] = Field(min_length=1)
    optional_fields: list[str] = Field(default_factory=list)
    forbidden_fields: list[str] = Field(default_factory=list)
    minimum_coverage: float = Field(default=1.0, ge=0, le=1)


class MappingTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    dataset_identity: dict[str, str]
    match: TemplateMatch
    adapter_parameters: dict[str, Any] = Field(default_factory=dict)
    field_mapping: FieldMappingSpecV2


@dataclass(frozen=True)
class ResolvedMappingTemplate:
    template_id: str
    template_version: str
    score: float
    mapping: FieldMappingSpecV2
    adapter_parameters: dict[str, Any]

    def provenance(self) -> dict[str, Any]:
        return {
            "source": "MAPPING_TEMPLATE",
            "template_id": self.template_id,
            "template_version": self.template_version,
            "match_score": self.score,
        }


class MappingTemplateResolver:
    def __init__(self, template_dir: Path | None = None) -> None:
        if template_dir is None:
            template_dir = (
                Path(__file__).resolve().parents[3] / "configs" / "mapping_templates"
            )
        self.template_dir = template_dir

    def load_templates(self) -> list[MappingTemplate]:
        if not self.template_dir.exists():
            return []
        return [
            MappingTemplate.model_validate(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.template_dir.glob("*.json"))
        ]

    def resolve(self, source_fields: list[str]) -> ResolvedMappingTemplate | None:
        available = set(source_fields)
        candidates: list[ResolvedMappingTemplate] = []
        for template in self.load_templates():
            required = set(template.match.required_fields)
            optional = set(template.match.optional_fields)
            if not required.issubset(available):
                continue
            if available.intersection(template.match.forbidden_fields):
                continue
            recognized = available.intersection(required | optional)
            coverage = len(recognized) / len(available) if available else 0.0
            if coverage < template.match.minimum_coverage:
                continue
            mapping = template.field_mapping.model_copy(
                update={
                    "mappings": [
                        rule
                        for rule in template.field_mapping.mappings
                        if rule.source_path.removeprefix("$.") in available
                    ]
                },
                deep=True,
            )
            candidates.append(
                ResolvedMappingTemplate(
                    template_id=template.template_id,
                    template_version=template.template_version,
                    score=coverage,
                    mapping=mapping,
                    adapter_parameters=template.adapter_parameters.copy(),
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.score, item.template_id))

    def explain(self, source_fields: list[str]) -> dict[str, Any]:
        resolved = self.resolve(source_fields)
        if resolved is not None:
            return {
                "matched": True,
                "reason": (
                    f"matched {resolved.template_id}@{resolved.template_version} "
                    f"with schema coverage {resolved.score:.3f}"
                ),
            }
        templates = self.load_templates()
        if not templates:
            return {"matched": False, "reason": "no mapping templates are installed"}
        available = set(source_fields)
        closest = min(
            templates,
            key=lambda template: len(set(template.match.required_fields) - available),
        )
        missing = sorted(set(closest.match.required_fields) - available)
        forbidden = sorted(available.intersection(closest.match.forbidden_fields))
        if missing:
            reason = f"closest template {closest.template_id} is missing required fields: {missing}"
        elif forbidden:
            reason = f"closest template {closest.template_id} matched forbidden fields: {forbidden}"
        else:
            recognized = available.intersection(
                set(closest.match.required_fields) | set(closest.match.optional_fields)
            )
            coverage = len(recognized) / len(available) if available else 0.0
            reason = (
                f"closest template {closest.template_id} schema coverage {coverage:.3f} "
                f"is below {closest.match.minimum_coverage:.3f}"
            )
        return {"matched": False, "reason": reason}

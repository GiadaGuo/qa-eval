from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2


class StructuralCompletenessCalculator:
    calculator_key = "structural_completeness"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "structural_completeness requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        role = parameters.get("content_role")
        field = parameters.get("metadata_field")
        if bool(role) == bool(field):
            raise CalculatorValidationError(
                "exactly one of content_role or metadata_field must be specified"
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        selector_kind = (
            "CONTENT_ROLE" if parameters.get("content_role") else "METADATA_FIELD"
        )
        selector = str(parameters.get("content_role") or parameters["metadata_field"])
        evidence: list[dict[str, Any]] = []
        counts = {"PRESENT": 0, "NULL": 0, "EMPTY_STRING": 0, "FIELD_ABSENT": 0}

        for line_number, raw_line in enumerate(
            context.input_path.read_text().splitlines(), 1
        ):
            if not raw_line.strip():
                continue
            try:
                sample = SampleRecordV2.model_validate_json(raw_line)
            except ValueError as exc:
                raise CalculatorValidationError(
                    "normalized input contains an invalid SampleRecord",
                    line_number=line_number,
                ) from exc
            category = self._category(sample, selector_kind, selector)
            counts[category] += 1
            if category != "PRESENT":
                evidence.append(
                    {
                        "sample_id": sample.sample_id,
                        "missing_category": category,
                        "selector_kind": selector_kind,
                        "selector": selector,
                    }
                )

        total = sum(counts.values())
        complete = counts["PRESENT"]
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "missingness.jsonl"
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in evidence
            ),
            encoding="utf-8",
        )
        observations: list[JsonObject] = [
            {
                "key": "complete_record_count",
                "value": complete,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "missing_record_count",
                "value": total - complete,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "evaluable_record_count",
                "value": total,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ]
        if total:
            observations.append(
                {
                    "key": "complete_record_ratio",
                    "value": complete / total,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": complete,
                    "denominator": total,
                }
            )
        return CalculatorOutput(
            observations=observations,
            population={
                "input_count": total,
                "evaluated_count": total,
                "excluded_count": 0,
                "exclusions": [],
            },
            semantic_coverage={
                "level": "FULL",
                "covered": ["field absent", "null", "empty string", "present value"],
                "excluded": ["user-approved reasonable missing range"],
                "limitations": ["quality thresholds remain a Quality Contract concern"],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="MISSINGNESS",
                    schema_ref="qa-eval://schemas/evidence/0204-missingness/1.0.0",
                    record_count=len(evidence),
                )
            ],
            diagnostics=[
                *diagnostics,
                {"key": "missing_category_counts", "counts": counts},
            ],
        )

    @staticmethod
    def _category(sample: SampleRecordV2, kind: str, selector: str) -> str:
        if kind == "METADATA_FIELD":
            if selector not in sample.metadata:
                return "FIELD_ABSENT"
            value = sample.metadata[selector]
            if value is None:
                return "NULL"
            if isinstance(value, str) and not value.strip():
                return "EMPTY_STRING"
            return "PRESENT"

        matches = [item for item in sample.contents if item.role == selector]
        if not matches:
            return "FIELD_ABSENT"
        if len(matches) > 1:
            raise CalculatorValidationError(
                "content role is ambiguous within a sample",
                sample_id=sample.sample_id,
                content_role=selector,
            )
        item = matches[0]
        if item.presence == "MISSING":
            return "FIELD_ABSENT"
        if item.presence == "NULL":
            return "NULL"
        if (
            isinstance(item.payload, InlineTextPayload)
            and not item.payload.text.strip()
        ):
            return "EMPTY_STRING"
        return "PRESENT"


__all__ = ["StructuralCompletenessCalculator"]

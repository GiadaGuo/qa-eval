"""Logical input adapter for the existing text-completeness measurement."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_completeness import (
    DEFAULT_TRUNCATION_SUFFIXES,
    TextCompletenessCalculator,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload


class LogicalTextCompletenessCalculator(TextCompletenessCalculator):
    version = "1.1.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (*super().source_paths(), Path(__file__))

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(
            replace(context, input_schema_ref="2.0"), parameters
        )
        role = str(parameters.get("content_role", "article.body.plain"))
        min_char_count = int(parameters.get("min_char_count", 50))
        suffixes = tuple(
            str(item)
            for item in parameters.get(
                "truncation_suffixes", list(DEFAULT_TRUNCATION_SUFFIXES)
            )
        )
        evidence: list[dict[str, Any]] = []
        reason_counts: dict[str, int] = {}
        total = 0

        if context.resolved_records is None:
            raise CalculatorValidationError("v3 Resolver is required")
        for record in context.resolved_records():
            total += 1
            item = record.values["body"]
            if item.status not in {"PRESENT", "EMPTY", "NULL", "MISSING"}:
                raise CalculatorValidationError(
                    "Unable to resolve text", reason=item.status
                )
            payload = (
                InlineTextPayload(type="inline_text", text=item.value, format="plain")
                if item.status in {"PRESENT", "EMPTY"}
                else None
            )
            sample = SimpleNamespace(
                sample_id=str(record.sample_id),
                contents=[
                    SimpleNamespace(role=role, presence=item.status, payload=payload)
                ],
            )
            reasons, char_count = self._incomplete_reasons(
                sample, role, min_char_count, suffixes
            )
            if reasons:
                for reason in reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                evidence.append(
                    {
                        "sample_id": sample.sample_id,
                        "reason_codes": reasons,
                        "normalized_char_count": char_count,
                        "content_role": role,
                    }
                )

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "text-completeness-violations.jsonl"
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in evidence
            ),
            encoding="utf-8",
        )
        incomplete = len(evidence)
        observations: list[JsonObject] = [
            {
                "key": "incomplete_text_record_count",
                "value": incomplete,
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
            observations.insert(
                0,
                {
                    "key": "incomplete_text_ratio",
                    "value": incomplete / total,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": incomplete,
                    "denominator": total,
                },
            )
        return CalculatorOutput(
            status="OK" if total else "NOT_APPLICABLE",
            observations=observations if total else [],
            population={
                "input_count": total,
                "evaluated_count": total,
                "excluded_count": 0,
                "exclusions": [],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "missing, null, and empty selected text",
                    "minimum non-whitespace character count",
                    "configured potential-truncation suffixes",
                ],
                "excluded": ["semantic completeness", "discourse-level truncation"],
                "limitations": [
                    "suffix matches are heuristic signals and may include intentional ellipses",
                    "minimum character count is a disclosed configurable rule, not a guide threshold",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="TEXT_COMPLETENESS_VIOLATIONS",
                    schema_ref="qa-eval://schemas/evidence/02080104-text-completeness/1.0.0",
                    record_count=incomplete,
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "text_completeness_rule_counts",
                    "counts": reason_counts,
                    "policy": {
                        "content_role": role,
                        "min_char_count": min_char_count,
                        "truncation_suffixes": list(suffixes),
                    },
                },
            ],
        )

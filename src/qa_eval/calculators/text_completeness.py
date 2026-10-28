from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

DEFAULT_TRUNCATION_SUFFIXES = ("...", "…", "[截断]", "[truncated]")


class TextCompletenessCalculator:
    """Apply disclosed length and potential-truncation rules to text records."""

    calculator_key = "text_completeness"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "text_completeness requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        min_char_count = parameters.get("min_char_count", 50)
        if isinstance(min_char_count, bool) or not isinstance(min_char_count, int):
            raise CalculatorValidationError("min_char_count must be an integer")
        if min_char_count < 1:
            raise CalculatorValidationError("min_char_count must be at least 1")
        suffixes = parameters.get(
            "truncation_suffixes", list(DEFAULT_TRUNCATION_SUFFIXES)
        )
        if not isinstance(suffixes, list) or not suffixes or not all(
            isinstance(item, str) and item for item in suffixes
        ):
            raise CalculatorValidationError(
                "truncation_suffixes must be a non-empty list of strings"
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
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

        with context.input_path.open(encoding="utf-8") as input_file:
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                total += 1
                try:
                    sample = SampleRecordV2.model_validate_json(raw_line)
                except ValidationError as exc:
                    raise CalculatorValidationError(
                        "normalized input contains an invalid SampleRecord",
                        line_number=line_number,
                    ) from exc

                reasons, char_count = self._incomplete_reasons(
                    sample, role, min_char_count, suffixes
                )
                if not reasons:
                    continue
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
            observations=observations,
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

    @staticmethod
    def _incomplete_reasons(
        sample: SampleRecordV2,
        role: str,
        min_char_count: int,
        suffixes: tuple[str, ...],
    ) -> tuple[list[str], int]:
        matches = [item for item in sample.contents if item.role == role]
        if not matches:
            return ["TEXT_ROLE_MISSING"], 0
        if len(matches) > 1:
            raise CalculatorValidationError(
                "content role is ambiguous within a sample",
                sample_id=sample.sample_id,
                content_role=role,
            )
        item = matches[0]
        if item.presence == "MISSING":
            return ["TEXT_ROLE_MISSING"], 0
        if item.presence == "NULL":
            return ["TEXT_NULL"], 0
        if not isinstance(item.payload, InlineTextPayload):
            raise CalculatorValidationError(
                "selected content role does not contain inline text",
                sample_id=sample.sample_id,
                content_role=role,
            )
        text = item.payload.text.strip()
        normalized_char_count = len("".join(text.split()))
        if not text:
            return ["TEXT_EMPTY"], 0
        reasons = []
        if normalized_char_count < min_char_count:
            reasons.append("BELOW_MIN_CHAR_COUNT")
        if text.casefold().endswith(tuple(item.casefold() for item in suffixes)):
            reasons.append("POTENTIAL_TRUNCATION_SUFFIX")
        return reasons, normalized_char_count


__all__ = ["TextCompletenessCalculator"]

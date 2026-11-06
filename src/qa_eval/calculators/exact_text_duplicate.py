from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_duplicate_algorithm import (
    _extract_text,
    normalize_text,
    sha256_text,
)
from qa_eval.contracts.base import JsonObject


class ExactTextDuplicateCalculator:
    calculator_key = "exact_text_duplicate"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "text_duplicate_algorithm.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "exact_text_duplicate requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        role = str(parameters.get("content_role", "article.body.plain"))
        groups: dict[str, list[str]] = defaultdict(list)
        excluded: list[dict[str, str]] = []
        input_count = 0
        for line_number, raw_line in enumerate(
            context.input_path.read_text().splitlines(), 1
        ):
            if not raw_line.strip():
                continue
            input_count += 1
            try:
                decoded = json.loads(raw_line)
                sample_id, value, _member = _extract_text(
                    decoded,
                    input_format="sample-record-v2",
                    content_role=role,
                    id_field="sample_id",
                    text_field="text",
                )
                normalized = normalize_text(value) if isinstance(value, str) else ""
                if not normalized:
                    excluded.append(
                        {"sample_id": sample_id, "reason": "EMPTY_OR_NULL_TEXT"}
                    )
                    continue
                groups[sha256_text(normalized)].append(sample_id)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CalculatorValidationError(
                    "normalized input cannot be evaluated",
                    line_number=line_number,
                    reason=str(exc),
                ) from exc

        duplicate_groups = [
            {
                "group_id": f"sha256:{digest}",
                "normalized_sha256": digest,
                "record_count": len(sample_ids),
                "representative_sample_id": sample_ids[0],
                "sample_ids": sample_ids,
            }
            for digest, sample_ids in sorted(groups.items())
            if len(sample_ids) > 1
        ]
        valid = sum(len(items) for items in groups.values())
        affected = sum(item["record_count"] for item in duplicate_groups)
        redundant = sum(item["record_count"] - 1 for item in duplicate_groups)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        path = context.output_dir / "exact_duplicate_groups.json"
        path.write_text(
            json.dumps(
                {"groups": duplicate_groups, "excluded": excluded},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        observations = []
        for key, numerator in (
            ("exact_affected_record_ratio", affected),
            ("exact_redundant_record_ratio", redundant),
        ):
            if valid:
                observations.append(
                    {
                        "key": key,
                        "value": numerator / valid,
                        "value_type": "NUMBER",
                        "unit": "ratio",
                        "numerator": numerator,
                        "denominator": valid,
                    }
                )
        return CalculatorOutput(
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": valid,
                "excluded_count": len(excluded),
                "exclusions": (
                    [{"reason": "EMPTY_OR_NULL_TEXT", "count": len(excluded)}]
                    if excluded
                    else []
                ),
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": ["normalized exact duplicate"],
                "excluded": [
                    "character n-gram",
                    "word n-gram",
                    "MinHash candidates",
                    "paragraph hash",
                    "semantic duplicate",
                ],
                "limitations": [
                    "only normalized whole-record exact duplicates are calculated"
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=path,
                    kind="DUPLICATE_GROUPS",
                    schema_ref="qa-eval://schemas/evidence/02080103-exact-duplicate-groups/1.0.0",
                    record_count=len(duplicate_groups),
                    error_count=len(excluded),
                )
            ],
            diagnostics=diagnostics,
        )


__all__ = ["ExactTextDuplicateCalculator"]

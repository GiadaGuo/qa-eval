from __future__ import annotations

from pathlib import Path
from typing import Any

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_duplicate_algorithm import calculate
from qa_eval.contracts.base import JsonObject


class TextDuplicateDegreeCalculator:
    calculator_key = "text_duplicate_degree"
    version = "2.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "text_duplicate_algorithm.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        permutations = int(parameters.get("minhash_permutations", 64))
        bands = int(parameters.get("lsh_bands", 16))
        if permutations % bands:
            raise CalculatorValidationError(
                "minhash_permutations must be evenly divisible by lsh_bands",
                minhash_permutations=permutations,
                lsh_bands=bands,
            )
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "text_duplicate_degree requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        legacy = calculate(
            context.input_path,
            context.output_dir,
            input_format="sample-record-v2",
            **parameters,
        )
        return CalculatorOutput(
            observations=self._observations(legacy),
            population={
                "input_count": legacy["input_record_count"],
                "evaluated_count": legacy["valid_record_count"],
                "excluded_count": legacy["anomaly_count"],
                "exclusions": []
                if not legacy["anomaly_count"]
                else [
                    {
                        "reason": "EMPTY_OR_INVALID_TEXT",
                        "count": legacy["anomaly_count"],
                    }
                ],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "exact duplicate",
                    "character n-gram",
                    "word n-gram",
                    "MinHash candidates",
                    "paragraph hash",
                ],
                "excluded": ["semantic duplicate", "sentence semantic duplicate"],
                "limitations": [
                    "near-duplicate outputs are candidates, not confirmed issues"
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=context.output_dir / "sample_features.jsonl",
                    kind="SAMPLE_FEATURES",
                    schema_ref="qa-eval://schemas/evidence/02080103-sample-features/2.0.0",
                    record_count=legacy["input_record_count"],
                    error_count=legacy["anomaly_count"],
                ),
                EvidenceOutput(
                    path=context.output_dir / "duplicate_groups.json",
                    kind="DUPLICATE_GROUPS",
                    schema_ref="qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0",
                    record_count=1,
                    error_count=legacy["anomaly_count"],
                ),
            ],
            diagnostics=diagnostics,
        )

    @staticmethod
    def _observations(value: dict[str, Any]) -> list[JsonObject]:
        specs = [
            (
                "exact_affected_record_ratio",
                "affected_record_ratio",
                "duplicate_record_count",
                "valid_record_count",
                "ratio",
            ),
            (
                "exact_redundant_record_ratio",
                "redundant_record_ratio",
                "redundant_record_count",
                "valid_record_count",
                "ratio",
            ),
            (
                "char_ngram_repetition_ratio",
                "char_rep_ratio",
                "char_repeated_ngram_frequency_sum",
                "char_ngram_total_count",
                "ratio",
            ),
            (
                "word_ngram_repetition_ratio",
                "word_rep_ratio",
                "word_repeated_ngram_frequency_sum",
                "word_ngram_total_count",
                "ratio",
            ),
            ("near_duplicate_candidate_record_count", None, None, None, "record"),
            (
                "paragraph_affected_ratio",
                "paragraph_affected_ratio",
                "repeated_paragraph_count",
                "paragraph_total_count",
                "ratio",
            ),
        ]
        items = []
        for key, value_key, numerator, denominator, unit in specs:
            observed = (
                value["minhash"]["near_duplicate_record_count"]
                if value_key is None
                else value[value_key]
            )
            if observed is None:
                continue
            item: JsonObject = {
                "key": key,
                "value": observed,
                "value_type": "NUMBER",
                "unit": unit,
            }
            if numerator is not None:
                item.update(
                    {"numerator": value[numerator], "denominator": value[denominator]}
                )
            items.append(item)
        return items


__all__ = ["TextDuplicateDegreeCalculator"]

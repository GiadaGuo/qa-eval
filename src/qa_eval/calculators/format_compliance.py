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
from qa_eval.contracts.datasets import SampleRecordV2


class FormatComplianceCalculator:
    """Evaluate the normalized JSONL envelope and SampleRecord 2.0 schema.

    This is deliberately a basic structural implementation of metric 0201. It
    does not claim that modality payloads conform to external domain formats.
    """

    calculator_key = "format_compliance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "format_compliance requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        if not context.input_path.is_file():
            raise CalculatorValidationError(
                "normalized input artifact is unavailable",
                input_path=str(context.input_path),
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        enforce_dataset_version = bool(parameters.get("enforce_dataset_version", True))
        require_unique_sample_id = bool(parameters.get("require_unique_sample_id", True))
        evidence: list[dict[str, Any]] = []
        seen_sample_ids: set[str] = set()
        total = 0
        compliant = 0
        violation_counts: dict[str, int] = {}

        with context.input_path.open(encoding="utf-8") as input_file:
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                total += 1
                sample_id: str | None = None
                violations: list[str] = []
                try:
                    document = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    document = None
                    violations.append("INVALID_JSON")

                if document is not None:
                    if not isinstance(document, dict):
                        violations.append("JSON_VALUE_NOT_OBJECT")
                    else:
                        raw_sample_id = document.get("sample_id")
                        if isinstance(raw_sample_id, str) and raw_sample_id:
                            sample_id = raw_sample_id
                        try:
                            sample = SampleRecordV2.model_validate(document)
                        except ValidationError:
                            sample = None
                            violations.append("SAMPLE_RECORD_SCHEMA_INVALID")
                        if sample is not None:
                            sample_id = sample.sample_id
                            if (
                                enforce_dataset_version
                                and sample.dataset_version_id
                                != context.dataset_version_id
                            ):
                                violations.append("DATASET_VERSION_MISMATCH")
                            if require_unique_sample_id and sample.sample_id in seen_sample_ids:
                                violations.append("DUPLICATE_SAMPLE_ID")
                            seen_sample_ids.add(sample.sample_id)

                if violations:
                    for code in violations:
                        violation_counts[code] = violation_counts.get(code, 0) + 1
                    item: dict[str, Any] = {
                        "line_number": line_number,
                        "violation_codes": violations,
                    }
                    if sample_id is not None:
                        item["sample_id"] = sample_id
                    evidence.append(item)
                else:
                    compliant += 1

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "format-violations.jsonl"
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in evidence
            ),
            encoding="utf-8",
        )
        observations: list[JsonObject] = [
            {
                "key": "compliant_record_count",
                "value": compliant,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "non_compliant_record_count",
                "value": total - compliant,
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
                    "key": "compliant_record_ratio",
                    "value": compliant / total,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": compliant,
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
                    "JSONL parseability",
                    "SampleRecord 2.0 schema",
                    "dataset version identity",
                    "sample ID uniqueness",
                ],
                "excluded": [
                    "external modality format standards",
                    "source artifact encoding before normalization",
                ],
                "limitations": [
                    "basic normalized-record rules only; thresholds remain a Quality Contract concern"
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="FORMAT_VIOLATIONS",
                    schema_ref="qa-eval://schemas/evidence/0201-format-violations/1.0.0",
                    record_count=len(evidence),
                    subject_scope="SAMPLE",
                )
            ],
            diagnostics=[
                *diagnostics,
                {"key": "format_violation_counts", "counts": violation_counts},
            ],
        )


__all__ = ["FormatComplianceCalculator"]

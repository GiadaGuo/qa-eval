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
from qa_eval.contracts.datasets import SampleRecordV2


def _records(context: CalculatorContext) -> list[SampleRecordV2]:
    if context.input_schema_ref != "2.0" or not context.input_path.is_file():
        raise CalculatorValidationError(
            "Calculator requires normalized SampleRecord 2.0"
        )
    records = []
    for line in context.input_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(SampleRecordV2.model_validate_json(line))
            except ValidationError as exc:
                raise CalculatorValidationError("Invalid SampleRecord input") from exc
    return records


def _evidence(
    context: CalculatorContext, name: str, rows: list[dict[str, Any]]
) -> EvidenceOutput:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    path = context.output_dir / f"{name}.jsonl"
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return EvidenceOutput(
        path=path,
        kind=name.upper().replace("-", "_"),
        schema_ref=f"qa-eval://schemas/evidence/{name}/1.0.0",
        record_count=len(rows),
    )


class AnnotationComplianceCalculator:
    calculator_key = "annotation_compliance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(self, context, parameters):
        _records(context)
        return []

    def execute(self, context, parameters):
        records = _records(context)
        annotated = [record for record in records if record.annotations]
        if not annotated:
            return CalculatorOutput(
                observations=[],
                population={
                    "input_count": len(records),
                    "evaluated_count": 0,
                    "excluded_count": len(records),
                    "exclusions": [
                        {"reason": "DATASET_HAS_NO_ANNOTATIONS", "count": len(records)}
                    ],
                },
                semantic_coverage={
                    "level": "PARTIAL",
                    "covered": ["标注结构、状态、标签集合"],
                    "excluded": ["标注准确率、多标注员一致性"],
                },
                status="NOT_APPLICABLE",
                status_detail={"reason_code": "UNANNOTATED_DATASET"},
            )
        allowed = set(parameters.get("allowed_labels", []))
        require_final = bool(parameters.get("require_final", True))
        failures = []
        compliant = 0
        for record in annotated:
            codes = []
            for annotation in record.annotations:
                if require_final and annotation.status != "FINAL":
                    codes.append("ANNOTATION_NOT_FINAL")
                label = annotation.value.get("label")
                if allowed and label not in allowed:
                    codes.append("LABEL_OUTSIDE_DECLARED_SET")
            if codes:
                failures.append(
                    {"sample_id": record.sample_id, "codes": sorted(set(codes))}
                )
            else:
                compliant += 1
        total = len(annotated)
        return CalculatorOutput(
            observations=[
                {
                    "key": "compliant_annotation_ratio",
                    "value": compliant / total,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": compliant,
                    "denominator": total,
                },
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
            ],
            population={
                "input_count": len(records),
                "evaluated_count": total,
                "excluded_count": len(records) - total,
                "exclusions": [],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": ["标注结构、状态、声明标签集合"],
                "excluded": ["标注准确率、多标注员一致性"],
            },
            status="PARTIAL",
            status_detail={"reason_code": "RULE_CHECKS_ONLY"},
            evidence=[_evidence(context, "0203-annotation-violations", failures)],
        )


class ContentAuthenticityCalculator:
    calculator_key = "content_authenticity"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(self, context, parameters):
        _records(context)
        return []

    def execute(self, context, parameters):
        records = _records(context)
        required_metadata = list(
            parameters.get("source_metadata_fields", ["origin_name", "url"])
        )
        rows = []
        traceable = 0
        for record in records:
            missing = [
                field for field in required_metadata if not record.metadata.get(field)
            ]
            has_locator = bool(record.source.locator or record.source.source_record_id)
            has_lineage = bool(record.lineage.input_fingerprint)
            if not missing and has_locator and has_lineage:
                traceable += 1
            else:
                rows.append(
                    {
                        "sample_id": record.sample_id,
                        "missing_metadata": missing,
                        "has_locator": has_locator,
                        "has_lineage": has_lineage,
                    }
                )
        total = len(records)
        observations = [
            {
                "key": "traceable_record_count",
                "value": traceable,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "unverifiable_record_count",
                "value": total - traceable,
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
                    "key": "traceable_record_ratio",
                    "value": traceable / total,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": traceable,
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
                "covered": ["来源字段、记录定位、血缘指纹的可追溯性"],
                "excluded": ["内容与源头一致性", "事实真实性", "生成数据分布真实性"],
            },
            status="PARTIAL" if total else "MISSING",
            status_detail={"reason_code": "PROVENANCE_PROXY_ONLY"},
            evidence=[_evidence(context, "0205-provenance-gaps", rows)],
            diagnostics=[{"code": "PROVENANCE_IS_NOT_AUTHENTICITY"}],
        )


class ContentCleanlinessAggregateCalculator:
    calculator_key = "content_cleanliness"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(self, context, parameters):
        return []

    def execute(self, context, parameters):
        del context, parameters
        return CalculatorOutput(
            observations=[],
            population={
                "input_count": 0,
                "evaluated_count": 0,
                "excluded_count": 0,
                "exclusions": [],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [],
                "excluded": ["02080101–02080107 正式可比评分聚合"],
            },
            status="MISSING",
            status_detail={
                "reason_code": "FORMAL_SCORE_MAPPING_UNAVAILABLE",
                "required_metrics": [f"0208010{i}" for i in range(1, 8)],
            },
            diagnostics=[{"code": "AGGREGATION_WITHHELD_NO_FORMAL_SCORE_MAPPING"}],
        )


__all__ = [
    "AnnotationComplianceCalculator",
    "ContentAuthenticityCalculator",
    "ContentCleanlinessAggregateCalculator",
]

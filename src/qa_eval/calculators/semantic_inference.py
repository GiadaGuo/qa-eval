from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject


class _SemanticInferenceCalculator:
    version = "1.0.0"
    calculator_key: ClassVar[str]
    output_field: ClassVar[str]
    score_key: ClassVar[str]
    ratio_key: ClassVar[str]
    count_key: ClassVar[str]
    default_threshold: ClassVar[float]
    ratio_when: ClassVar[str]
    coverage: ClassVar[list[str]]

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        del context, parameters
        return []

    def score_shared(
        self,
        context: CalculatorContext,
        parameters: JsonObject,
        records: list[JsonObject],
    ) -> CalculatorOutput:
        threshold = float(parameters.get("classification_threshold", self.default_threshold))
        values: list[float] = []
        evidence_rows: list[dict[str, Any]] = []
        exclusions: dict[str, int] = {}
        for record in records:
            output = record.get("outputs", {}).get(self.output_field)
            if not isinstance(output, dict) or not isinstance(output.get("score"), (int, float)):
                reason = str(record.get("error_code") or "OUTPUT_MISSING")
                exclusions[reason] = exclusions.get(reason, 0) + 1
                continue
            score = float(output["score"])
            if not 0 <= score <= 1:
                exclusions["SCORE_OUT_OF_RANGE"] = exclusions.get("SCORE_OUT_OF_RANGE", 0) + 1
                continue
            values.append(score)
            evidence_rows.append(
                {
                    "sample_id": record.get("sample_id"),
                    "score": score,
                    "rationale": output.get("rationale"),
                    "flags": output.get("flags", []),
                    "inference_id": record.get("inference_id"),
                }
            )
        input_count = len(records)
        evaluated_count = len(values)
        excluded_count = input_count - evaluated_count
        if evaluated_count == 0:
            raise ValueError(f"No evaluable shared inference output for {self.calculator_key}")
        matched = sum(
            value >= threshold if self.ratio_when == "GTE" else value < threshold
            for value in values
        )
        mean = sum(values) / evaluated_count
        observations = self._observations(mean, matched, evaluated_count)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / f"{self.calculator_key}-records.jsonl"
        evidence_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in evidence_rows),
            encoding="utf-8",
        )
        return CalculatorOutput(
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": evaluated_count,
                "excluded_count": excluded_count,
                "exclusions": [
                    {"reason": reason, "count": count}
                    for reason, count in sorted(exclusions.items())
                ],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": self.coverage,
                "excluded": ["全文未超出配置上下文窗口的完整性保证"],
                "limitations": ["基于声明式 Rubric 的模型语义判断"],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="SHARED_INFERENCE_METRIC_EVIDENCE",
                    schema_ref="qa-eval://schemas/evidence/shared-inference-metric/1.0",
                    record_count=evaluated_count,
                    error_count=excluded_count,
                    attributes={"threshold": threshold, "output_field": self.output_field},
                )
            ],
            diagnostics=[{"code": "SHARED_INFERENCE_SCORED", "excluded": excluded_count}],
        )

    def _observations(
        self, mean: float, matched: int, evaluated_count: int
    ) -> list[JsonObject]:
        return [
            {
                "key": self.score_key,
                "value": mean,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": mean * evaluated_count,
                "denominator": evaluated_count,
            },
            {
                "key": self.ratio_key,
                "value": matched / evaluated_count,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": matched,
                "denominator": evaluated_count,
            },
            {"key": self.count_key, "value": matched, "value_type": "NUMBER", "unit": "record"},
            {
                "key": "evaluable_record_count",
                "value": evaluated_count,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ]


class KnowledgeInformationDensityCalculator(_SemanticInferenceCalculator):
    calculator_key = "knowledge_information_density"
    output_field = "knowledge_density"
    score_key = "mean_effective_knowledge_score"
    ratio_key = "low_density_record_ratio"
    count_key = "low_density_record_count"
    default_threshold = 0.4
    ratio_when = "LT"
    coverage: ClassVar[list[str]] = ["有效事实丰富度", "领域相关性", "信息冗余度"]

    def _observations(self, mean: float, matched: int, evaluated_count: int) -> list[JsonObject]:
        return [
            {"key": "mean_effective_knowledge_score", "value": mean, "value_type": "NUMBER", "unit": "ratio", "numerator": mean * evaluated_count, "denominator": evaluated_count},
            {"key": "low_density_record_ratio", "value": matched / evaluated_count, "value_type": "NUMBER", "unit": "ratio", "numerator": matched, "denominator": evaluated_count},
            {"key": "evaluable_record_count", "value": evaluated_count, "value_type": "NUMBER", "unit": "record"},
        ]


class InformationMissingDegreeCalculator(_SemanticInferenceCalculator):
    calculator_key = "information_missing_degree"
    output_field = "information_missing"
    score_key = "mean_information_missing_degree"
    ratio_key = "key_element_missing_ratio"
    count_key = "key_element_missing_record_count"
    default_threshold = 0.5
    ratio_when = "GTE"
    coverage: ClassVar[list[str]] = ["关键要素缺失", "异常截断", "语义信息空洞"]

    def _observations(self, mean: float, matched: int, evaluated_count: int) -> list[JsonObject]:
        del mean
        return [
            {"key": "key_element_missing_ratio", "value": matched / evaluated_count, "value_type": "NUMBER", "unit": "ratio", "numerator": matched, "denominator": evaluated_count},
            {"key": "key_element_missing_record_count", "value": matched, "value_type": "NUMBER", "unit": "record"},
            {"key": "evaluable_record_count", "value": evaluated_count, "value_type": "NUMBER", "unit": "record"},
        ]


class TextCoherenceCalculator(_SemanticInferenceCalculator):
    calculator_key = "text_coherence"
    output_field = "text_coherence"
    score_key = "mean_coherence_score"
    ratio_key = "coherent_text_ratio"
    count_key = "incoherent_text_record_count"
    default_threshold = 0.6
    ratio_when = "GTE"
    coverage: ClassVar[list[str]] = ["主题一致性", "句段衔接", "异常跳跃与截断"]

    def _observations(self, mean: float, matched: int, evaluated_count: int) -> list[JsonObject]:
        incoherent = evaluated_count - matched
        return [
            {
                "key": "coherent_text_ratio",
                "value": matched / evaluated_count,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": matched,
                "denominator": evaluated_count,
            },
            {
                "key": "incoherent_text_record_count",
                "value": incoherent,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "evaluable_record_count",
                "value": evaluated_count,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ]

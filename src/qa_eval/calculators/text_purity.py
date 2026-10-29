"""文本纯净程度计算器（02080106，规则子集实现）。

实现范围与边界：

- 仅实现指标规范中可由**确定性规则**识别的显式干扰内容（乱码、特殊符号
  堆积、HTML 残留、页码/页眉页脚残留、显式广告标记），规则集见
  :mod:`qa_eval.calculators.text_purity_rules`；
- 隐性广告、软文、语义性污染等判断属于 Shared LLM Stage 规划范围，
  本计算器**不实现**也不模拟，结果 ``semantic_coverage.level`` 固定为
  ``PARTIAL``，下游不得将其当作 02080106 的完整评分；
- 聚合语义严格区分三个量：
  ``rule_hit_count``（各规则命中次数合计）≠ ``interference_record_count``
  （去重后的受影响记录数）；比率分母为可评估记录数（排除缺失/NULL/空文本），
  同一记录命中多条规则只计一次。

融合接口（预留）：证据文件按 ``sample_id`` 组织、每条命中携带
``detection_source``（本实现恒为 ``"RULE"``）与规则版本；后续 Hybrid
计算器可按 ``sample_id`` 将 RULE 命中与 SEMANTIC 命中做并集融合。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_purity_rules import (
    DEFAULT_AD_MARKER_PATTERNS,
    DEFAULT_LAYOUT_RESIDUE_PATTERNS,
    RULESET_VERSION,
    PurityRule,
    PurityRuleConfig,
    PurityRuleHit,
    build_rules,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2


def _string_list(parameters: JsonObject, key: str) -> tuple[str, ...] | None:
    value = parameters.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CalculatorValidationError(
            f"{key} must be a non-empty list of non-empty strings"
        )
    return tuple(value)


class TextPurityCalculator:
    """Deterministic explicit-interference rules over one text content role."""

    calculator_key = "text_purity"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "text_purity_rules.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "text_purity requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        known_ids = {rule.rule_id for rule in build_rules(PurityRuleConfig())}
        enabled = _string_list(parameters, "enabled_rules")
        if enabled is not None:
            unknown = sorted(set(enabled) - known_ids)
            if unknown:
                raise CalculatorValidationError(
                    "enabled_rules references unknown rule ids",
                    unknown_rules=unknown,
                    known_rules=sorted(known_ids),
                )
        config = self._rule_config(parameters)
        for key in ("ad_marker_patterns", "layout_residue_patterns"):
            for pattern in getattr(config, key):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise CalculatorValidationError(
                        f"{key} contains an invalid regular expression",
                        pattern=pattern,
                        error=str(exc),
                    ) from exc
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        role = str(parameters.get("content_role", "article.body.plain"))
        config = self._rule_config(parameters)
        enabled_ids = _string_list(parameters, "enabled_rules")
        rules = [
            rule
            for rule in build_rules(config)
            if enabled_ids is None or rule.rule_id in enabled_ids
        ]

        input_count = 0
        exclusions: dict[str, int] = {}
        affected_records: list[dict[str, Any]] = []
        rule_hit_totals: dict[str, int] = {}
        rule_record_totals: dict[str, int] = {}

        with context.input_path.open(encoding="utf-8") as input_file:
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                input_count += 1
                try:
                    sample = SampleRecordV2.model_validate_json(raw_line)
                except ValidationError as exc:
                    raise CalculatorValidationError(
                        "normalized input contains an invalid SampleRecord",
                        line_number=line_number,
                    ) from exc

                text, exclude_reason = self._select_text(sample, role)
                if exclude_reason is not None:
                    exclusions[exclude_reason] = exclusions.get(exclude_reason, 0) + 1
                    continue
                assert text is not None

                hits = [hit for hit in (rule.detect(text) for rule in rules) if hit]
                if not hits:
                    continue
                for hit in hits:
                    rule_hit_totals[hit.rule_id] = (
                        rule_hit_totals.get(hit.rule_id, 0) + hit.count
                    )
                    rule_record_totals[hit.rule_id] = (
                        rule_record_totals.get(hit.rule_id, 0) + 1
                    )
                affected_records.append(
                    {
                        "sample_id": sample.sample_id,
                        "content_role": role,
                        "hit_count": sum(hit.count for hit in hits),
                        "hits": [hit.to_evidence_json() for hit in hits],
                    }
                )

        evaluated_count = input_count - sum(exclusions.values())
        affected_count = len(affected_records)
        observations: list[JsonObject] = [
            {
                "key": "interference_record_count",
                "value": affected_count,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "evaluable_record_count",
                "value": evaluated_count,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "rule_hit_count",
                "value": sum(rule_hit_totals.values()),
                "value_type": "NUMBER",
                "unit": "hit",
            },
        ]
        if evaluated_count:
            observations.insert(
                0,
                {
                    "key": "interference_record_ratio",
                    "value": affected_count / evaluated_count,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": affected_count,
                    "denominator": evaluated_count,
                },
            )

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "purity_violations.jsonl"
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in affected_records
            ),
            encoding="utf-8",
        )

        rule_stats = {
            rule.rule_id: {
                "hit_count": rule_hit_totals.get(rule.rule_id, 0),
                "affected_record_count": rule_record_totals.get(rule.rule_id, 0),
                "rule_version": rule.rule_version,
                "description": rule.description,
            }
            for rule in rules
        }
        return CalculatorOutput(
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": evaluated_count,
                "excluded_count": sum(exclusions.values()),
                "exclusions": [
                    {"reason": reason, "count": count}
                    for reason, count in sorted(exclusions.items())
                ],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "显式干扰内容的确定性规则识别（乱码、特殊符号堆积、"
                    "HTML 残留、页码/页眉页脚残留、显式广告标记）"
                ],
                "excluded": [
                    "隐性广告与软文的语义判断（规划由 Shared LLM Stage 补充）",
                    "语义性污染与上下文相关的排版噪声判定",
                ],
                "limitations": [
                    "规则结果仅为 02080106 的显式子集，不可当作完整文本纯净程度评分",
                    "规则命中是候选证据，允许人工复核后确认或驳回",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="TEXT_PURITY_VIOLATIONS",
                    schema_ref=(
                        "qa-eval://schemas/evidence/02080106-text-purity/1.0.0"
                    ),
                    record_count=affected_count,
                    error_count=0,
                    redaction_policy="matched-excerpts-only",
                    attributes={
                        "ruleset_version": RULESET_VERSION,
                        "rule_stats": rule_stats,
                    },
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "text_purity_rule_stats",
                    "ruleset_version": RULESET_VERSION,
                    "rule_stats": rule_stats,
                    "policy": {
                        "content_role": role,
                        "enabled_rules": [rule.rule_id for rule in rules],
                        "garbled_char_ratio_threshold": (
                            config.garbled_char_ratio_threshold
                        ),
                        "symbol_run_length": config.symbol_run_length,
                        "symbol_density_threshold": config.symbol_density_threshold,
                        "html_tag_count_threshold": config.html_tag_count_threshold,
                        "ad_marker_patterns": list(config.ad_marker_patterns),
                        "layout_residue_patterns": list(config.layout_residue_patterns),
                    },
                    "counting_semantics": {
                        "rule_hit_count": "所有规则命中次数之和（同一记录可贡献多次）",
                        "interference_record_count": "命中至少一条规则的记录数（按记录去重）",
                        "denominator": "evaluated_count = 输入记录 - 缺失/NULL/空文本记录",
                    },
                },
            ],
        )

    @staticmethod
    def _rule_config(parameters: JsonObject) -> PurityRuleConfig:
        def number(key: str, minimum: float, default: float) -> float:
            value = parameters.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CalculatorValidationError(f"{key} must be a number")
            if value < minimum:
                raise CalculatorValidationError(
                    f"{key} must be at least {minimum}", **{key: value}
                )
            return value

        ad_patterns = _string_list(parameters, "ad_marker_patterns")
        layout_patterns = _string_list(parameters, "layout_residue_patterns")
        return PurityRuleConfig(
            garbled_char_ratio_threshold=number(
                "garbled_char_ratio_threshold", 0.0, 0.01
            ),
            symbol_run_length=int(number("symbol_run_length", 2, 6)),
            symbol_density_threshold=number("symbol_density_threshold", 0.0, 0.05),
            html_tag_count_threshold=int(number("html_tag_count_threshold", 1, 3)),
            ad_marker_patterns=ad_patterns or DEFAULT_AD_MARKER_PATTERNS,
            layout_residue_patterns=layout_patterns or DEFAULT_LAYOUT_RESIDUE_PATTERNS,
        )

    @staticmethod
    def _select_text(
        sample: SampleRecordV2, role: str
    ) -> tuple[str | None, str | None]:
        """返回 (文本, 排除原因)；排除原因非空时文本为 None。"""

        matches = [item for item in sample.contents if item.role == role]
        if not matches:
            return None, "TEXT_ROLE_MISSING"
        if len(matches) > 1:
            raise CalculatorValidationError(
                "content role is ambiguous within a sample",
                sample_id=sample.sample_id,
                content_role=role,
            )
        item = matches[0]
        if item.presence == "MISSING":
            return None, "TEXT_ROLE_MISSING"
        if item.presence == "NULL":
            return None, "TEXT_NULL"
        if not isinstance(item.payload, InlineTextPayload):
            raise CalculatorValidationError(
                "selected content role does not contain inline text",
                sample_id=sample.sample_id,
                content_role=role,
            )
        text = item.payload.text
        if not text.strip():
            return None, "TEXT_EMPTY"
        return text, None


__all__ = ["TextPurityCalculator", "PurityRule", "PurityRuleHit"]

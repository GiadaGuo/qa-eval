"""Text purity rules over the resolved plain-text representation."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from qa_eval.calculators.protocol import (
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_purity import TextPurityCalculator, _string_list
from qa_eval.calculators.text_purity_rules import RULESET_VERSION, build_rules
from qa_eval.contracts.base import JsonObject


class LogicalTextPurityCalculator(TextPurityCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (*super().source_paths(), Path(__file__))

    def validate(self, context, parameters):
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("text_purity@1.1.0 requires a v3 Resolver")
        return super().validate(replace(context, input_schema_ref="2.0"), parameters)

    def execute(self, context, parameters):
        diagnostics = self.validate(context, parameters)
        return self._evaluate_texts(
            context, parameters, diagnostics, "article.body.plain", self._resolved_texts(context),
        )

    @staticmethod
    def _resolved_texts(context):
        exclusions = {"MISSING": "TEXT_ROLE_MISSING", "NULL": "TEXT_NULL", "EMPTY": "TEXT_EMPTY"}
        for record in context.resolved_records():
            value = record.values["body"]
            reason = exclusions.get(value.status)
            if reason is None:
                if value.status != "PRESENT" or not isinstance(value.value, str):
                    raise CalculatorValidationError("Unable to resolve text", reason=value.status)
                if not value.value.strip():
                    reason = "TEXT_EMPTY"
            provenance = {
                "source_origin": asdict(value.origin),
                "coordinate_space": "RESOLVED_TEXT",
                "representation": "text/plain",
            } if value.origin else {}
            yield str(record.sample_id), value.value, reason, provenance

    def _evaluate_texts(self, context, parameters, diagnostics, role, texts):
        config = self._rule_config(parameters)
        enabled_ids = _string_list(parameters, "enabled_rules")
        rules = [
            rule
            for rule in build_rules(config)
            if enabled_ids is None or rule.rule_id in enabled_ids
        ]

        input_count = 0
        exclusions: dict[str, int] = {}
        affected_count = 0
        rule_hit_totals: dict[str, int] = {}
        rule_record_totals: dict[str, int] = {}

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "purity_violations.jsonl"
        with evidence_path.open("w", encoding="utf-8") as evidence_file:
            for sample_id, text, exclude_reason, location in texts:
                input_count += 1
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
                affected_count += 1
                evidence_file.write(json.dumps(
                    {
                        "sample_id": sample_id,
                        "content_role": role,
                        "hit_count": sum(hit.count for hit in hits),
                        "hits": [hit.to_evidence_json() for hit in hits],
                        **location,
                    }, ensure_ascii=False, sort_keys=True,
                ) + "\n")

        evaluated_count = input_count - sum(exclusions.values())
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
                    ("显式干扰内容的确定性规则识别（乱码、特殊符号堆积、"
                     "HTML 残留、页码/页眉页脚残留、显式广告标记）")
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

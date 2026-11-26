"""Score Resolver text with a checksum-pinned KenLM model."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_perplexity import (
    MAX_PERPLEXITY,
    TOKENIZER_VERSION,
    TextPerplexityCalculator,
    _load_model,
    _percentile,
    _tokenize,
    score_text,
)
from qa_eval.contracts.base import JsonObject


class LogicalTextPerplexityCalculator(TextPerplexityCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (*super().source_paths(), Path(__file__))

    def validate(self, context, parameters):
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("text_perplexity requires a v3 Resolver")
        model = context.input_artifacts.get("KENLM_MODEL")
        if model is None:
            raise CalculatorValidationError("Frozen KENLM_MODEL is required")
        return super().validate(
            replace(context, input_schema_ref="2.0"),
            {**parameters, "model_path": str(model)},
        )

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        tokenizer = str(parameters.get("tokenizer", "han_character"))
        min_token_count = int(parameters.get("min_token_count", 2))
        high_threshold = float(parameters.get("high_perplexity_threshold", 1000.0))
        model_path = context.input_artifacts["KENLM_MODEL"]
        assert model_path is not None  # validate 已保证
        model, model_fingerprint = _load_model(model_path)
        model_order = int(getattr(model, "order", 0) or 0)

        input_count = 0
        exclusions: dict[str, int] = {}
        samples: list[dict[str, Any]] = []
        evaluated: list[dict[str, Any]] = []
        oov_token_total = 0
        token_total = 0

        for sample in context.resolved_records():
            input_count += 1
            value = sample.values["body"]
            text = value.value
            exclude_reason = {
                "MISSING": "TEXT_ROLE_MISSING",
                "NULL": "TEXT_NULL",
                "EMPTY": "TEXT_EMPTY",
            }.get(value.status)
            if value.status == "PRESENT" and not text.strip():
                exclude_reason = "TEXT_EMPTY"
            elif exclude_reason is None and value.status != "PRESENT":
                raise CalculatorValidationError(
                    "Unable to resolve body", reason=value.status
                )
            if exclude_reason is not None:
                exclusions[exclude_reason] = exclusions.get(exclude_reason, 0) + 1
                continue
            assert text is not None

            tokens = _tokenize(text, tokenizer)
            if len(tokens) < min_token_count:
                exclusions["TOO_FEW_TOKENS"] = exclusions.get("TOO_FEW_TOKENS", 0) + 1
                continue
            perplexity, total_log10, event_count, oov_count = score_text(model, tokens)
            oov_token_total += oov_count
            token_total += len(tokens)
            record = {
                "sample_id": str(sample.sample_id),
                "status": "EVALUATED",
                "source_origin": asdict(value.origin),
                "token_count": len(tokens),
                "event_count": event_count,
                "log10_probability": round(total_log10, 6),
                "perplexity": perplexity,
                "oov_token_count": oov_count,
            }
            samples.append(record)
            evaluated.append(record)

        evaluated_count = len(evaluated)
        perplexities = sorted(item["perplexity"] for item in evaluated)
        observations: list[JsonObject] = [
            {
                "key": "evaluable_record_count",
                "value": evaluated_count,
                "value_type": "NUMBER",
                "unit": "record",
            }
        ]
        if evaluated_count:
            mean = sum(perplexities) / evaluated_count
            high_count = sum(1 for value in perplexities if value >= high_threshold)
            observations = [
                {
                    "key": "mean_perplexity",
                    "value": mean,
                    "value_type": "NUMBER",
                    "unit": "perplexity",
                },
                {
                    "key": "high_perplexity_record_ratio",
                    "value": high_count / evaluated_count,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": high_count,
                    "denominator": evaluated_count,
                },
                {
                    "key": "perplexity_median",
                    "value": _percentile(perplexities, 0.5),
                    "value_type": "NUMBER",
                    "unit": "perplexity",
                },
                {
                    "key": "perplexity_p90",
                    "value": _percentile(perplexities, 0.90),
                    "value_type": "NUMBER",
                    "unit": "perplexity",
                },
                {
                    "key": "perplexity_p95",
                    "value": _percentile(perplexities, 0.95),
                    "value_type": "NUMBER",
                    "unit": "perplexity",
                },
                {
                    "key": "oov_token_ratio",
                    "value": (oov_token_total / token_total) if token_total else 0.0,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": oov_token_total,
                    "denominator": token_total,
                },
                observations[0],
            ]

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "perplexity_samples.jsonl"
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in samples
            ),
            encoding="utf-8",
        )

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
                "level": "FULL",
                "covered": [
                    (
                        "KenLM 统计语言模型困惑度（log10 正确换算，"
                        "BOS 仅作条件、EOS 计入事件数）"
                    )
                ],
                "excluded": [],
                "limitations": [
                    "PPL 依赖所配置语言模型的训练语料，不同模型间的绝对值不可直接比较",
                    (
                        "指标规范未定义 PPL→质量分转换，本结果仅输出原始 PPL 统计，"
                        "不构成 0~1 质量分"
                    ),
                    "未登录词按 <unk> 计分，oov_token_ratio 暴露模型覆盖率",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="PERPLEXITY_SAMPLES",
                    schema_ref=(
                        "qa-eval://schemas/evidence/02080101-perplexity-samples/1.0.0"
                    ),
                    record_count=evaluated_count,
                    error_count=sum(exclusions.values()),
                    attributes={
                        "model_fingerprint": model_fingerprint,
                        "model_order": model_order,
                        "tokenizer": tokenizer,
                        "tokenizer_version": (
                            TOKENIZER_VERSION
                            if tokenizer == "han_character"
                            else "whitespace_v1"
                        ),
                    },
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "text_perplexity_model_info",
                    "model_path": str(model_path),
                    "model_fingerprint": model_fingerprint,
                    "model_order": model_order,
                    "tokenizer": tokenizer,
                    "bos_eos_policy": "BOS conditioning-only; EOS counted as event (N = tokens + 1)",
                    "formula": "PPL = 10 ** (-log10_total / N)",
                    "percentile_method": "nearest-rank",
                    "high_perplexity_threshold": high_threshold,
                    "min_token_count": min_token_count,
                    "evaluated_token_count": token_total,
                    "oov_token_count": oov_token_total,
                    "max_perplexity_cap": MAX_PERPLEXITY,
                },
            ],
        )

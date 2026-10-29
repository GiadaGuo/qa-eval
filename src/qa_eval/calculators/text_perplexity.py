"""文本困惑程度计算器（02080101，KenLM 实现）。

计算口径（与 KenLM 官方 ``query`` 工具约定一致）：

- ``model.score(text, bos=True, eos=True)`` 返回以 **10 为底** 的对数概率；
  PPL = ``10 ** (-log10_total / N)``，其中 ``N = token 数 + 1``
  （``</s>`` 作为被预测事件计入分母，``<s>`` 仅作条件前缀、不计数）。
  不得把 log10 分数直接代入自然指数公式；
- 分词策略必须与语言模型训练语料的分词一致：``han_character`` 将每个汉字
  作为独立 token（与 02080103 的 stdlib 词法一致），``whitespace`` 按空白
  切分（适用于以空格预分词语料训练的模型）。模型不匹配时结果无意义，
  结果中随附 OOV 比例以暴露覆盖情况；
- 空文本/缺失/NULL/低于 ``min_token_count`` 的极短文本不进入 PPL 总体，
  计入 population exclusions；
- KenLM 将未登录词映射为 ``<unk>`` 计分，OOV token 数单独统计。

输出边界：**PPL 原始值不是 0~1 质量分**。指标规范未定义 PPL→质量分
转换，本计算器只输出原始 PPL 统计（mean/median/P90/P95 与高困惑记录
占比），不构造任何映射分。

模型管理：模型路径来自 ``model_path`` 参数或 ``QA_EVAL_KENLM_MODEL_PATH``
配置；模型按进程缓存复用（键含 mtime/size，替换模型文件后自动重载）。
缺少依赖或模型时抛出带明确 code 的 :class:`CalculatorError`，绝不生成
模拟 PPL。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorError,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.config import get_settings
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

TOKENIZER_VERSION = "stdlib_unicode_lexical_han_character_tokens_v1"
HAN_CHARACTER_PATTERN = re.compile(r"[A-Za-z0-9]+|[㐀-䶿一-鿿]")
MAX_PERPLEXITY = 1e300


class KenlmDependencyError(CalculatorError):
    code = "KENLM_DEPENDENCY_UNAVAILABLE"


class KenlmModelError(CalculatorError):
    code = "KENLM_MODEL_UNAVAILABLE"


# 进程级模型缓存：键 = (realpath, mtime_ns, size)，避免每条记录重复加载。
_MODEL_CACHE: dict[tuple[str, int, int], Any] = {}
_MODEL_FINGERPRINTS: dict[tuple[str, int, int], str] = {}


def _tokenize(text: str, tokenizer: str) -> list[str]:
    if tokenizer == "whitespace":
        return text.split()
    return HAN_CHARACTER_PATTERN.findall(text)


def _load_model(model_path: Path) -> tuple[Any, str]:
    """加载并缓存 KenLM 模型，返回 (model, sha256 指纹)。"""

    try:
        import kenlm
    except ImportError as exc:  # pragma: no cover - 由 Loader 依赖声明先行拦截
        raise KenlmDependencyError(
            "The kenlm package is required by text_perplexity",
            install_hint="uv sync --extra kenlm",
        ) from exc

    if not model_path.is_file():
        raise KenlmModelError(
            "KenLM model file is missing",
            model_path=str(model_path),
        )
    stat = model_path.stat()
    key = (str(model_path.resolve()), stat.st_mtime_ns, stat.st_size)
    model = _MODEL_CACHE.get(key)
    if model is None:
        try:
            model = kenlm.Model(str(model_path))
        except OSError as exc:
            raise KenlmModelError(
                "KenLM model cannot be loaded",
                model_path=str(model_path),
                cause=str(exc),
            ) from exc
        _MODEL_CACHE.clear()  # 同一路径只保留最新版本
        _MODEL_CACHE[key] = model
    fingerprint = _MODEL_FINGERPRINTS.get(key)
    if fingerprint is None:
        digest = hashlib.sha256()
        with model_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprint = f"sha256:{digest.hexdigest()}"
        _MODEL_FINGERPRINTS.clear()
        _MODEL_FINGERPRINTS[key] = fingerprint
    return model, fingerprint


def _resolve_model_path(parameters: JsonObject) -> Path | None:
    configured = parameters.get("model_path") or get_settings().kenlm_model_path
    if not configured or not str(configured).strip():
        return None
    return Path(str(configured))


def score_text(
    model: Any, tokens: list[str]
) -> tuple[float, float, int, int]:
    """返回 (PPL, total_log10, event_count, oov_token_count)。

    ``event_count = len(tokens) + 1``（含 ``</s>``；``<s>`` 仅作条件）。
    逐 token 求和与 ``model.score`` 一致，同时取得 OOV 标记。
    """

    if not tokens:
        raise ValueError("token sequence must not be empty")
    scored = " ".join(tokens)
    total_log10 = 0.0
    oov_count = 0
    for log10_prob, _, is_oov in model.full_scores(scored, bos=True, eos=True):
        total_log10 += log10_prob
        if is_oov:
            oov_count += 1
    event_count = len(tokens) + 1
    try:
        perplexity = math.pow(10.0, -total_log10 / event_count)
    except OverflowError:
        perplexity = math.inf
    return min(perplexity, MAX_PERPLEXITY), total_log10, event_count, oov_count


def _percentile(sorted_values: list[float], quantile: float) -> float:
    """nearest-rank 百分位：index = ceil(q * n) - 1（0-based）。"""

    rank = max(1, math.ceil(quantile * len(sorted_values)))
    return sorted_values[rank - 1]


class TextPerplexityCalculator:
    """KenLM perplexity over one text content role."""

    calculator_key = "text_perplexity"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "text_perplexity requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        tokenizer = parameters.get("tokenizer", "han_character")
        if tokenizer not in {"han_character", "whitespace"}:
            raise CalculatorValidationError(
                "tokenizer must be 'han_character' or 'whitespace'",
                tokenizer=str(tokenizer),
            )
        min_token_count = parameters.get("min_token_count", 2)
        if (
            isinstance(min_token_count, bool)
            or not isinstance(min_token_count, int)
            or min_token_count < 1
        ):
            raise CalculatorValidationError(
                "min_token_count must be an integer >= 1"
            )
        threshold = parameters.get("high_perplexity_threshold", 1000.0)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or threshold <= 0
        ):
            raise CalculatorValidationError(
                "high_perplexity_threshold must be a positive number"
            )
        if _resolve_model_path(parameters) is None:
            raise KenlmModelError(
                "No KenLM model configured; refusing to fabricate perplexity",
                hint="Set the model_path parameter or QA_EVAL_KENLM_MODEL_PATH",
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        role = str(parameters.get("content_role", "article.body.plain"))
        tokenizer = str(parameters.get("tokenizer", "han_character"))
        min_token_count = int(parameters.get("min_token_count", 2))
        high_threshold = float(
            parameters.get("high_perplexity_threshold", 1000.0)
        )
        model_path = _resolve_model_path(parameters)
        assert model_path is not None  # validate 已保证
        model, model_fingerprint = _load_model(model_path)
        model_order = int(getattr(model, "order", 0) or 0)

        input_count = 0
        exclusions: dict[str, int] = {}
        samples: list[dict[str, Any]] = []
        evaluated: list[dict[str, Any]] = []
        oov_token_total = 0
        token_total = 0

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

                tokens = _tokenize(text, tokenizer)
                if len(tokens) < min_token_count:
                    exclusions["TOO_FEW_TOKENS"] = (
                        exclusions.get("TOO_FEW_TOKENS", 0) + 1
                    )
                    continue
                perplexity, total_log10, event_count, oov_count = score_text(
                    model, tokens
                )
                oov_token_total += oov_count
                token_total += len(tokens)
                record = {
                    "sample_id": sample.sample_id,
                    "status": "EVALUATED",
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
            high_count = sum(
                1 for value in perplexities if value >= high_threshold
            )
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
                    "KenLM 统计语言模型困惑度（log10 正确换算，"
                    "BOS 仅作条件、EOS 计入事件数）"
                ],
                "excluded": [],
                "limitations": [
                    "PPL 依赖所配置语言模型的训练语料，不同模型间的绝对值不可直接比较",
                    "指标规范未定义 PPL→质量分转换，本结果仅输出原始 PPL 统计，"
                    "不构成 0~1 质量分",
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
                            TOKENIZER_VERSION if tokenizer == "han_character" else "whitespace_v1"
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


__all__ = [
    "KenlmDependencyError",
    "KenlmModelError",
    "TextPerplexityCalculator",
    "score_text",
]

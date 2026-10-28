# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 文档评测器：逐指标「LLM 结构化调用 → Pydantic 解析 → 程序校验」，
# 校验失败带错误反馈重试（至多 max_validation_retries 次），
# 仍失败该指标记 ERROR 而非 0 分（D6）。
# ====================================================

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from qa_eval.application.document_evaluation.parser import ParsedDocument
from qa_eval.application.document_evaluation.prompts import (
    SYSTEM_PROMPT,
    build_metric_user_prompt,
)
from qa_eval.application.document_evaluation.validator import (
    LLMMetricJudgment,
    validate_metric_judgment,
)
from qa_eval.infrastructure.llm.client import (
    LLMError,
    LLMTransportError,
)

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "1.0.0"


@dataclass
class MetricEvaluationOutcome:
    metric_code: str
    metric_name: str
    status: str = "ERROR"  # OK | ERROR
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    llm_calls: int = 0
    validation_retries: int = 0


@dataclass(frozen=True)
class EvaluationRunResult:
    outcomes: list[MetricEvaluationOutcome]
    limitations: list[str]


class DocumentEvaluator:
    """纯流水线编排，不接触数据库；LLM 客户端经 Protocol 注入。"""

    def __init__(
        self,
        llm_client: Any,
        *,
        max_validation_retries: int = 2,
        max_prompt_lines: int = 4000,
        max_prompt_chars: int = 400_000,
    ) -> None:
        self._llm = llm_client
        self._max_validation_retries = max_validation_retries
        self._max_prompt_lines = max_prompt_lines
        self._max_prompt_chars = max_prompt_chars

    def evaluate(
        self,
        rubric: dict[str, Any],
        parsed: ParsedDocument,
        on_metric: Callable[[MetricEvaluationOutcome], None] | None = None,
    ) -> EvaluationRunResult:
        limitations: list[str] = []
        truncated_after_line = self._truncate_limit(parsed.lines)
        if truncated_after_line is not None:
            limitations.append(
                f"文档共 {parsed.line_count} 行，超过 prompt 上限，"
                f"仅前 {truncated_after_line} 行参与判定，"
                "其余内容未评测（document-only 截断）"
            )

        outcomes: list[MetricEvaluationOutcome] = []
        for rubric_metric in rubric["metrics"]:
            outcome = self.evaluate_metric(
                rubric_metric,
                parsed,
                truncated_after_line=truncated_after_line,
            )
            outcomes.append(outcome)
            if on_metric is not None:
                on_metric(outcome)
        return EvaluationRunResult(outcomes=outcomes, limitations=limitations)

    def evaluate_metric(
        self,
        rubric_metric: dict[str, Any],
        parsed: ParsedDocument,
        *,
        truncated_after_line: int | None = None,
    ) -> MetricEvaluationOutcome:
        code = rubric_metric["metric_code"]
        outcome = MetricEvaluationOutcome(
            metric_code=code, metric_name=rubric_metric["metric_name"]
        )
        last_line = truncated_after_line or parsed.line_count
        numbered = parsed.render_numbered(last_line)

        feedback: list[str] = []
        attempts = self._max_validation_retries + 1
        for attempt in range(1, attempts + 1):
            prompt = build_metric_user_prompt(
                rubric_metric=rubric_metric,
                numbered_document=numbered,
                document_sha256=parsed.sha256,
                line_count=parsed.line_count,
                truncated_after_line=truncated_after_line,
                previous_errors=feedback or None,
            )
            outcome.llm_calls += 1
            try:
                payload = self._llm.complete_json(system=SYSTEM_PROMPT, user=prompt)
                judgment = LLMMetricJudgment.model_validate(payload)
            except LLMTransportError as exc:
                # 传输级失败已在客户端重试过，不消耗校验重试额度
                outcome.status = "ERROR"
                outcome.error = f"LLM 网关传输失败（客户端重试后仍失败）：{exc}"
                logger.warning("Document metric %s transport failure: %s", code, exc)
                return outcome
            except (LLMError, ValidationError) as exc:
                feedback = [f"第 {attempt} 次输出解析失败：{_summarize(exc)}"]
                logger.info(
                    "Document metric %s attempt %d parse failed: %s",
                    code,
                    attempt,
                    feedback[0],
                )
                continue

            errors = validate_metric_judgment(judgment, rubric_metric, parsed.lines)
            if not errors:
                outcome.status = "OK"
                outcome.items = [item.model_dump() for item in judgment.items]
                return outcome

            outcome.validation_retries += 1
            feedback = errors
            logger.info(
                "Document metric %s attempt %d validation failed: %s",
                code,
                attempt,
                "; ".join(errors),
            )

        outcome.status = "ERROR"
        outcome.error = (
            f"校验重试耗尽（{attempts} 次尝试均未通过程序校验）；"
            f"最后一次错误：{'；'.join(feedback[:3])}"
        )
        return outcome

    def _truncate_limit(self, lines: tuple[str, ...]) -> int | None:
        """按行数与字符数两个上限计算截断行号；无需截断返回 None。"""

        line_count = len(lines)
        limit = min(line_count, self._max_prompt_lines)
        total_chars = sum(len(line) for line in lines[:limit])
        while limit > 0 and total_chars > self._max_prompt_chars:
            limit -= 1
            total_chars -= len(lines[limit])
        return None if limit == line_count else limit


def _summarize(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        messages = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:3]
        ]
        return "; ".join(messages)
    return str(exc)

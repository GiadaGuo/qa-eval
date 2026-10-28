# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# LLM 判定输出的程序校验器（移植自 Skill 的 validate_result.py）：
# 结构用 Pydantic 强校验；语义（rubric 一致性、证据行号范围、
# 摘录逐字命中、MISSING 必有 inspected scope）由本模块校验。
# 分数一律由程序复算，LLM 输出不含任何分数字段。
# ====================================================

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from qa_eval.contracts.document import DocumentEvidence


class LLMItemJudgment(BaseModel):
    """LLM 单检查项判定的结构契约（extra=forbid，多余字段即失败）。"""

    model_config = ConfigDict(extra="forbid")

    item_code: str = Field(min_length=1)
    status: Literal["SATISFIED", "INSUFFICIENT", "MISSING", "UNVERIFIABLE"]
    document_evidence: list[DocumentEvidence] = Field(default_factory=list)
    inspected_scope: str
    reason: str
    suggestion: str
    expected_section: str | None = None
    fixability: Literal["AUTO_FIXABLE", "DATA_DERIVABLE", "REQUIRES_HUMAN_INPUT"]


class LLMMetricJudgment(BaseModel):
    """LLM 单指标判定（一次调用返回该指标 5 个检查项）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[LLMItemJudgment] = Field(min_length=5, max_length=5)


def validate_metric_judgment(
    judgment: LLMMetricJudgment,
    rubric_metric: dict[str, Any],
    lines: Sequence[str],
) -> list[str]:
    """返回校验错误列表；空列表表示通过。

    rubric_metric 为冻结 rubric JSON 中的单个指标段
    （metric_code / metric_name / items[{item_code, item_name, ...}]）。
    """

    errors: list[str] = []
    rubric_items = rubric_metric["items"]
    prefix = f"metric {rubric_metric['metric_code']}"

    expected_codes = [item["item_code"] for item in rubric_items]
    actual_codes = [item.item_code for item in judgment.items]
    if actual_codes != expected_codes:
        errors.append(
            f"{prefix}: item codes/order mismatch, "
            f"expected {expected_codes}, got {actual_codes}"
        )

    for index, item in enumerate(judgment.items):
        item_prefix = f"item {item.item_code}"
        if index < len(rubric_items):
            rubric_item = rubric_items[index]
            if item.item_code != rubric_item["item_code"]:
                continue  # 顺序错误已在上面报告

        if not item.inspected_scope.strip():
            errors.append(f"{item_prefix}: inspected_scope is required")
        if not item.reason.strip():
            errors.append(f"{item_prefix}: reason is required")
        if not item.suggestion.strip():
            errors.append(f"{item_prefix}: suggestion is required")

        if item.status == "MISSING":
            if item.document_evidence:
                errors.append(
                    f"{item_prefix}: MISSING items must not carry document evidence"
                )
            if not (item.expected_section and item.expected_section.strip()):
                errors.append(
                    f"{item_prefix}: MISSING items must provide expected_section"
                )
        else:
            if not item.document_evidence:
                errors.append(
                    f"{item_prefix}: non-missing judgment needs document evidence"
                )

        for evidence_index, evidence in enumerate(item.document_evidence):
            evidence_prefix = f"{item_prefix} evidence[{evidence_index}]"
            if evidence.line_end > len(lines):
                errors.append(
                    f"{evidence_prefix}: line_end {evidence.line_end} "
                    f"exceeds document line_count {len(lines)}"
                )
                continue
            cited = "\n".join(lines[evidence.line_start - 1 : evidence.line_end])
            if evidence.excerpt not in cited:
                errors.append(
                    f"{evidence_prefix}: excerpt not found verbatim "
                    f"in lines {evidence.line_start}-{evidence.line_end}"
                )

    return errors

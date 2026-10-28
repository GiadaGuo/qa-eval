# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档评测 prompt 组装（rubric 单指标 + 行号化全文 + 输出结构）。
# ====================================================

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "1.0.0"

SYSTEM_PROMPT = """你是数据集说明文档质量评测专家。依据给定的评测标准（rubric）对 Markdown 说明文档逐项做语义充分性判定，只判定、不重写文档。

判定状态（每项必选其一，禁止输出 ERROR——那是执行状态，由程序产生）：
- SATISFIED：实质内容达到该检查项的最低标准。
- INSUFFICIENT：文档提及了主题，但内容模糊、不完整、不可操作或仅为占位。
- MISSING：检查全部给定文档内容后，确认不含相关内容。
- UNVERIFIABLE：事实性声明需外部证据核实真伪，文档内无法裁定，且本轮不提供外部证据。

硬性边界（违反即校验失败）：
1. 判定依据只能是给定文档的编号行；不得用文档外知识补全或推断内容。
2. document_evidence.excerpt 必须逐字摘自 line_start–line_end 连续区间内的原文，不得改写、翻译、省略或拼接不相邻内容。
3. 非 MISSING 判定必须给出至少一条文档证据；MISSING 不给文档证据，但必须填写 inspected_scope（已检查范围）和 expected_section（建议插入位置）。
4. 严禁编造许可、联系人、授权范围、基准结果或任何事实；文档没有就按 MISSING / UNVERIFIABLE 处理。
5. 每项必须填写 inspected_scope、reason、suggestion（SATISFIED 项的 suggestion 可为「无需修改」）；fixability 只能取 AUTO_FIXABLE / DATA_DERIVABLE / REQUIRES_HUMAN_INPUT。
6. 输出必须是一个 JSON 对象，不含规定之外的键；items 必须恰好 5 项，item_code 与顺序和评测标准完全一致。"""

OUTPUT_CONTRACT = """输出 JSON 结构（items 恰好 5 项，顺序与评测标准一致）：
{
  "items": [
    {
      "item_code": "0101-01",
      "status": "SATISFIED | INSUFFICIENT | MISSING | UNVERIFIABLE",
      "document_evidence": [{"line_start": 12, "line_end": 12, "excerpt": "逐字摘录"}],
      "inspected_scope": "检查了哪些内容/章节后得出该结论",
      "expected_section": "仅 MISSING 必填：建议插入的章节，其余可为 null",
      "reason": "判定依据",
      "suggestion": "可执行的修改建议",
      "fixability": "AUTO_FIXABLE | DATA_DERIVABLE | REQUIRES_HUMAN_INPUT"
    }
  ]
}"""


def _render_rubric_metric(rubric_metric: dict[str, Any]) -> str:
    lines = [
        f"## 评测标准：{rubric_metric['metric_code']} {rubric_metric['metric_name']}"
    ]
    for item in rubric_metric["items"]:
        lines.append(f"\n### {item['item_code']} {item['item_name']}")
        lines.append(f"- 满足标准：{item['satisfied_standard']}")
        if item.get("counterexamples"):
            for counter in item["counterexamples"]:
                lines.append(f"- 反例（不满足）：{counter}")
        if item.get("notes"):
            lines.append(f"- 备注：{item['notes']}")
    return "\n".join(lines)


def build_metric_user_prompt(
    *,
    rubric_metric: dict[str, Any],
    numbered_document: str,
    document_sha256: str,
    line_count: int,
    truncated_after_line: int | None = None,
    previous_errors: list[str] | None = None,
) -> str:
    """组装单指标评测的 user prompt；重试时附带程序校验错误反馈。"""

    sections = [_render_rubric_metric(rubric_metric)]

    header = f"## 待评测文档（SHA-256 {document_sha256[:12]}…，共 {line_count} 行）"
    if truncated_after_line is not None:
        header += (
            f"\n注意：文档过长，已截断，仅提供前 {truncated_after_line} 行；"
            "截断范围之外的内容未参与判定。"
        )
    sections.append(f"{header}\n\n{numbered_document}")

    if previous_errors:
        feedback = "\n".join(f"- {error}" for error in previous_errors)
        sections.append(
            f"## 上次输出未通过程序校验，必须修正以下问题后重新输出\n{feedback}"
        )

    sections.append(f"## 输出要求\n{OUTPUT_CONTRACT}")
    return "\n\n".join(sections)

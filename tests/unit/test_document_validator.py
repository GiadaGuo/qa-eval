# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 判定校验器单测：golden 通过 + 篡改行号/摘录、枚举顺序、
# MISSING 语义等负例。
# ====================================================

import pytest

from qa_eval.application.document_evaluation.validator import (
    LLMItemJudgment,
    LLMMetricJudgment,
    validate_metric_judgment,
)

LINES = (
    "# 数据集说明",
    "",
    "本数据集包含 1,000 条记录。",
    "## 数据规模",
    "共 1,000 条问答对，train/dev/test 划分为 8:1:1。",
)


def _rubric_metric() -> dict:
    return {
        "metric_code": "0101",
        "metric_name": "基本信息完整性",
        "items": [
            {
                "item_code": f"0101-0{index}",
                "item_name": f"检查项{index}",
                "satisfied_standard": "标准",
                "counterexamples": [],
                "notes": None,
            }
            for index in range(1, 6)
        ],
    }


def _item(index: int, **overrides) -> dict:
    fields = {
        "item_code": f"0101-0{index}",
        "status": "SATISFIED",
        "document_evidence": [
            {
                "line_start": 3,
                "line_end": 3,
                "excerpt": "本数据集包含 1,000 条记录。",
            }
        ],
        "inspected_scope": "检查全文",
        "reason": "内容达标",
        "suggestion": "无需修改",
        "expected_section": None,
        "fixability": "AUTO_FIXABLE",
    }
    fields.update(overrides)
    return fields


def _judgment(items: list[dict]) -> LLMMetricJudgment:
    return LLMMetricJudgment.model_validate(
        {"items": [LLMItemJudgment.model_validate(item) for item in items]}
    )


def test_golden_judgment_passes():
    judgment = _judgment([_item(index) for index in range(1, 6)])
    assert validate_metric_judgment(judgment, _rubric_metric(), LINES) == []


def test_tampered_line_number_rejected():
    items = [
        _item(
            1,
            document_evidence=[
                {"line_start": 3, "line_end": 99, "excerpt": "本数据集包含"}
            ],
        )
    ]
    items += [_item(i) for i in range(2, 6)]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("exceeds document line_count" in e for e in errors)


def test_tampered_excerpt_rejected():
    items = [
        _item(
            1,
            document_evidence=[
                {
                    "line_start": 3,
                    "line_end": 3,
                    "excerpt": "本数据集包含 10,000 条记录。",
                }
            ],
        )
    ]
    items += [_item(i) for i in range(2, 6)]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("not found verbatim" in e for e in errors)


def test_excerpt_must_not_cross_nonadjacent_lines():
    # 摘录拼接了不相邻两行的内容
    items = [
        _item(
            1,
            document_evidence=[
                {
                    "line_start": 1,
                    "line_end": 3,
                    "excerpt": "# 数据集说明本数据集包含 1,000 条记录。",
                }
            ],
        )
    ]
    items += [_item(i) for i in range(2, 6)]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("not found verbatim" in e for e in errors)


def test_item_code_order_mismatch_rejected():
    items = [_item(i) for i in range(1, 6)]
    items[0], items[1] = items[1], items[0]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("codes/order mismatch" in e for e in errors)


def test_missing_item_semantics_enforced():
    # MISSING 不给证据、必须给 expected_section
    items = [
        _item(1, status="MISSING", document_evidence=[]),
        *[_item(i) for i in range(2, 6)],
    ]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("expected_section" in e for e in errors)

    # MISSING 携带证据同样非法
    items = [
        _item(1, status="MISSING", expected_section="建议章节"),
        *[_item(i) for i in range(2, 6)],
    ]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("must not carry" in e for e in errors)

    # 合法 MISSING
    items = [
        _item(
            1,
            status="MISSING",
            document_evidence=[],
            expected_section="六、技术支持",
        ),
        *[_item(i) for i in range(2, 6)],
    ]
    assert validate_metric_judgment(_judgment(items), _rubric_metric(), LINES) == []


def test_non_missing_without_evidence_rejected():
    items = [_item(1, status="INSUFFICIENT", document_evidence=[])]
    items += [_item(i) for i in range(2, 6)]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("needs document evidence" in e for e in errors)


def test_blank_required_text_rejected():
    items = [_item(1, reason="   ")]
    items += [_item(i) for i in range(2, 6)]
    errors = validate_metric_judgment(_judgment(items), _rubric_metric(), LINES)
    assert any("reason is required" in e for e in errors)


def test_extra_field_rejected_by_strict_contract():
    payload = _item(1)
    payload["item_name"] = "越权字段"
    with pytest.raises((ValueError, TypeError)):
        LLMItemJudgment.model_validate(payload)

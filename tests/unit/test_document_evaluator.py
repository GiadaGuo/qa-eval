# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 文档评测器单测：成功、带校验反馈重试成功、重试耗尽记
# ERROR 而非 0 分、传输失败立即 ERROR、截断披露与调用统计。
# ====================================================

import re

from qa_eval.application.document_evaluation.evaluator import (
    DocumentEvaluator,
)
from qa_eval.application.document_evaluation.parser import parse_markdown
from qa_eval.infrastructure.llm.client import LLMTransportError

CONTENT = (
    "# 数据集说明\n"
    "\n"
    "本数据集包含 1,000 条记录。\n"
    "## 数据规模\n"
    "train/dev/test 划分为 8:1:1。\n"
).encode()

_METRIC_HEADER = re.compile(r"## 评测标准：(\d{4}) ")
_ITEM_HEADER = re.compile(r"### (\d{4}-\d{2}) ")
_NUMBERED = re.compile(r"^(\d+)\|(.*)$", re.MULTILINE)


def _rubric_metric(code: str = "0101") -> dict:
    return {
        "metric_code": code,
        "metric_name": "基本信息完整性",
        "items": [
            {
                "item_code": f"{code}-0{index}",
                "item_name": f"检查项{index}",
                "satisfied_standard": "标准",
                "counterexamples": [],
                "notes": None,
            }
            for index in range(1, 6)
        ],
    }


def _valid_payload(user_prompt: str, statuses=None) -> dict:
    """依据 prompt 内的编号行生成可通过程序校验的判定。"""

    numbered = _NUMBERED.findall(user_prompt)
    first_line, first_text = numbered[0]
    codes = _ITEM_HEADER.findall(user_prompt)
    statuses = statuses or ["SATISFIED"] * 5
    items = []
    for code, status in zip(codes, statuses, strict=True):
        if status == "MISSING":
            items.append(
                {
                    "item_code": code,
                    "status": status,
                    "document_evidence": [],
                    "inspected_scope": "检查全文未发现相关内容",
                    "expected_section": "十、附录",
                    "reason": "文档缺少该内容",
                    "suggestion": "补充对应章节",
                    "fixability": "REQUIRES_HUMAN_INPUT",
                }
            )
        else:
            items.append(
                {
                    "item_code": code,
                    "status": status,
                    "document_evidence": [
                        {
                            "line_start": int(first_line),
                            "line_end": int(first_line),
                            "excerpt": first_text,
                        }
                    ],
                    "inspected_scope": "检查全文",
                    "reason": "内容达标",
                    "suggestion": "无需修改",
                    "expected_section": None,
                    "fixability": "AUTO_FIXABLE",
                }
            )
    return {"items": items}


class ScriptedLLM:
    """脚本化 LLM：默认按 prompt 生成合法判定，可注入失败模式。"""

    model = "mock-model"
    transport_retries = 1

    def __init__(self, modes: dict[str, str] | None = None):
        # mode: "ok" | "invalid" | "invalid_then_ok" | "transport"
        self.modes = modes or {}
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.prompts.append(user)
        code = _METRIC_HEADER.search(user).group(1)
        mode = self.modes.get(code, "ok")
        if mode == "transport":
            raise LLMTransportError("connection reset by peer")
        if mode == "invalid":
            return _tampered(user)
        if mode == "invalid_then_ok" and self._calls_for(code) == 1:
            return _tampered(user)
        return _valid_payload(user)

    def _calls_for(self, code: str) -> int:
        return sum(1 for prompt in self.prompts if f"## 评测标准：{code} " in prompt)

    def prompts_for(self, code: str) -> list[str]:
        return [p for p in self.prompts if f"## 评测标准：{code} " in p]


def _tampered(user_prompt: str) -> dict:
    """结构合法但 item_code 全错：通过 Pydantic、败于程序校验。"""

    payload = _valid_payload(user_prompt)
    for item in payload["items"]:
        item["item_code"] = "0000-99"
    return payload


def _parsed():
    return parse_markdown(CONTENT)


def test_success_returns_ok_items():
    llm = ScriptedLLM()
    evaluator = DocumentEvaluator(llm, max_validation_retries=2)

    outcome = evaluator.evaluate_metric(_rubric_metric(), _parsed())

    assert outcome.status == "OK"
    assert len(outcome.items) == 5
    assert outcome.llm_calls == 1
    assert outcome.validation_retries == 0
    assert outcome.error is None


def test_retry_with_feedback_recovers():
    llm = ScriptedLLM({"0101": "invalid_then_ok"})
    evaluator = DocumentEvaluator(llm, max_validation_retries=2)

    outcome = evaluator.evaluate_metric(_rubric_metric(), _parsed())

    assert outcome.status == "OK"
    assert outcome.llm_calls == 2
    assert outcome.validation_retries == 1
    # 第二次 prompt 必须携带程序校验错误反馈
    second_prompt = llm.prompts_for("0101")[1]
    assert "未通过程序校验" in second_prompt
    assert "codes/order mismatch" in second_prompt


def test_retry_exhaustion_marks_error_not_zero():
    llm = ScriptedLLM({"0101": "invalid"})
    evaluator = DocumentEvaluator(llm, max_validation_retries=2)

    outcome = evaluator.evaluate_metric(_rubric_metric(), _parsed())

    assert outcome.status == "ERROR"
    assert outcome.items == []
    assert "校验重试耗尽" in outcome.error
    assert outcome.llm_calls == 3  # 1 + 2 次重试
    assert len(llm.prompts_for("0101")) == 3


def test_transport_failure_is_immediate_error():
    llm = ScriptedLLM({"0101": "transport"})
    evaluator = DocumentEvaluator(llm, max_validation_retries=2)

    outcome = evaluator.evaluate_metric(_rubric_metric(), _parsed())

    assert outcome.status == "ERROR"
    assert "传输失败" in outcome.error
    assert outcome.llm_calls == 1  # 不消耗校验重试额度
    assert len(llm.prompts_for("0101")) == 1


def test_evaluate_iterates_all_metrics_with_progress_callback():
    llm = ScriptedLLM()
    evaluator = DocumentEvaluator(llm, max_validation_retries=1)
    rubric = {"metrics": [_rubric_metric("0101"), _rubric_metric("0102")]}

    seen: list[str] = []
    result = evaluator.evaluate(
        rubric, _parsed(), on_metric=lambda o: seen.append(o.metric_code)
    )

    assert [o.status for o in result.outcomes] == ["OK", "OK"]
    assert seen == ["0101", "0102"]
    assert result.limitations == []
    assert len(llm.prompts) == 2


def test_truncation_is_disclosed_and_applied():
    llm = ScriptedLLM()
    evaluator = DocumentEvaluator(llm, max_validation_retries=1, max_prompt_lines=3)

    result = evaluator.evaluate({"metrics": [_rubric_metric()]}, _parsed())

    assert any("仅前 3 行" in note for note in result.limitations)
    prompt = llm.prompts[0]
    assert "已截断" in prompt
    # 编号行只到第 3 行
    assert _NUMBERED.findall(prompt)[-1] == ("3", "本数据集包含 1,000 条记录。")


def test_prompt_contains_rubric_and_output_contract():
    llm = ScriptedLLM()
    evaluator = DocumentEvaluator(llm, max_validation_retries=1)

    evaluator.evaluate_metric(_rubric_metric(), _parsed())

    prompt = llm.prompts[0]
    assert "## 评测标准：0101 基本信息完整性" in prompt
    assert "0101-01 检查项1" in prompt
    assert "输出 JSON 结构" in prompt
    assert "1|# 数据集说明" in prompt

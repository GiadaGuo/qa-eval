"""R6.1 input, output, and deterministic fallback for single-metric reports."""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from qa_eval.infrastructure.llm.client import LLMResponseError

VERSION = "r6.1"
STATISTICS = {
    "input_count": ("输入记录", "count"),
    "compliant_record_count": ("合规记录", "count"),
    "not_evaluated_count": ("未判定记录", "count"),
    "compliant_record_ratio": ("合规率", "ratio"),
    "evaluated_compliance_ratio": ("已评测合规率", "ratio"),
}
GATE_LABELS = {"PASS": "通过", "FAIL": "未通过", "NOT_APPLICABLE": "不适用"}
REASONS = {
    "signoff/SIGNOFF_NOT_CORROBORATED": "落款未获印证",
    "N8/PHYSICAL_LAYOUT_UNOBSERVABLE": "版面不可观测",
    "N3/SIGNED_DATE_NOT_CORROBORATED": "成文日期未获印证",
    "N1/REVIEW_REQUIRED": "标题需人工复核",
    "N2/NO_CORROBORATED_ISSUE_NUMBER": "未找到可印证的发文字号",
    "N4/ATTACHMENT_PAIR_UNVERIFIED": "附件对应关系未核实",
    "N5/REVIEW_REQUIRED": "规范条目需人工复核",
    "TEMPLATE_PATH_UNCONFIRMED": "模板路径未确认",
    "BELOW_MIN_COVERAGE": "可判定内容低于最低覆盖要求",
    "INSUFFICIENT_INDEPENDENT_EVIDENCE": "独立证据不足",
    "TYPE_UNCONFIRMED": "公文类型未确认",
}


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=16)
    content: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    evidence_refs: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_plain_text(self):
        for value in (self.title, self.content):
            if not re.search(r"[\u3400-\u9fff]", value) or re.search(r"[A-Za-z_\n\r`*#<>|]", value):
                raise ValueError("Expected plain Chinese text")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate evidence reference")
        return self


class Risk(Point):
    content: Annotated[str, StringConstraints(min_length=1, max_length=50)]


class Recommendation(Point):
    priority: Literal["HIGH", "MEDIUM", "LOW"]


class Sections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusions: list[Point] = Field(min_length=1, max_length=2)
    causes: list[Point] = Field(max_length=3)
    risks: list[Risk] = Field(max_length=2)
    recommendations: list[Recommendation] = Field(max_length=3)

    def points(self):
        return [*self.conclusions, *self.causes, *self.risks, *self.recommendations]

    @model_validator(mode="after")
    def validate_body(self):
        contents = [re.sub(r"[\W_]", "", point.content) for point in self.points()]
        if sum(len(point.content) for point in self.points()) > 580 or len(set(contents)) != len(contents):
            raise ValueError("Body exceeds limit or repeats a point")
        return self


def build_facts(report, results):
    facts = {
        "gate": {"label": "报告门禁", "value": GATE_LABELS.get(report.gate_status, "未判定")},
        "violations": {"label": "确认违规", "value": report.issue_summary.confirmed_count, "unit": "count"},
    }
    for index, metric in enumerate(getattr(report, "metrics", []), start=1):
        measurement = metric.measurement
        if measurement is not None and type(measurement.value) in (int, float):
            facts[f"metric{index}"] = {
                "label": metric.metric.name,
                "value": measurement.value,
                "unit": "ratio" if measurement.unit == "ratio" else None,
            }
    for index, section in enumerate(getattr(report, "native_evaluations", []), start=1):
        rule = section.evaluation
        label = STATISTICS.get(rule.statistic_ref.statistics_key, ("指标", ""))[0]
        operator = {"GTE": "≥", "GT": ">", "LTE": "≤", "LT": "<", "EQ": "=", "NE": "≠"}.get(rule.rule.operator)
        unit = getattr(rule.statistic.unit, "value", rule.statistic.unit)
        facts[f"rule{index}"] = {
            "label": f"{label}门禁",
            "value": GATE_LABELS.get(rule.status, "未判定"),
            "actual": rule.metric_value,
            "expected": rule.rule.expected,
            "operator": operator,
            "unit": "ratio" if unit == "ratio" else None,
        }
    for index, result in enumerate(results, start=1):
        if result.calculator_key != "policy_document_format":
            continue
        for stat_index, (key, (label, unit)) in enumerate(STATISTICS.items(), start=1):
            value = result.statistics.get(key)
            if type(value) in (int, float):
                facts[f"stat{index}_{stat_index}"] = {"label": label, "value": value, "unit": unit}
        counts = result.statistics.get("unknown_reasons")
        if isinstance(counts, dict):
            ranked = sorted(
                ((key, value) for key, value in counts.items() if key in REASONS and type(value) is int and value > 0),
                key=lambda pair: (-pair[1], pair[0]),
            )
            for rank, (key, count) in enumerate(ranked[:3], start=1):
                facts[f"reason{index}_{rank}"] = {"label": REASONS[key], "value": count, "unit": "frequency"}
    return facts


def _fallback(facts):
    reason_refs = [key for key in facts if key.startswith("reason")][:3]
    has_undetermined = any(
        fact["label"] == "未判定记录" and fact["value"] > 0 for fact in facts.values()
    )
    return Sections(
        conclusions=[Point(
            title="报告门禁结果", content=f"本次评测门禁{facts['gate']['value']}，以报告冻结判定为准。",
            evidence_refs=["gate"],
        )],
        causes=[Point(
            title="未判定原因", content="依据中列出主要记录原因，可逐项核查对应证据。",
            evidence_refs=reason_refs,
        )] if reason_refs else [],
        risks=[Risk(
            title="未判定范围", content="未判定记录的合规状态仍待确认。",
            evidence_refs=[next(key for key, fact in facts.items() if fact["label"] == "未判定记录" and fact["value"] > 0)],
        )] if has_undetermined else [],
        recommendations=[Recommendation(
            title="复核判定依据", content="优先抽查未判定样本，结合原文与冻结规则确认判定条件。",
            evidence_refs=reason_refs[:1], priority="HIGH",
        )] if reason_refs else [],
    )


def generate(facts, client_factory):
    client = None
    degraded = False
    try:
        client = client_factory()
        system = (
            f"任务版本 {VERSION}。使用简洁中文解读单指标质量报告，只返回符合结构约束的 JSON。"
            "每条一个观点，跨模块不重复，不要求填满。正文合计最多580字；只用输入中的引用ID，"
            "不要复述统计数值或技术键，不写Markdown。区分确认违规、未判定和潜在风险；"
            "原因频次可重叠，信号未定位不能推断为原始数据缺失。禁止猜测根因、放宽阈值或修改结论。"
            "整改优先级 HIGH、MEDIUM、LOW 分别表示高、中、低。"
            f"结构约束：{json.dumps(Sections.model_json_schema(), ensure_ascii=False)}"
        )
        user = json.dumps(facts, ensure_ascii=False)
        for attempt in range(2):
            try:
                sections = Sections.model_validate(client.complete_json(system=system, user=user))
                if any(ref not in facts for point in sections.points() for ref in point.evidence_refs):
                    raise ValueError("Unknown evidence reference")
                break
            except (ValidationError, LLMResponseError, ValueError):
                if attempt == 1:
                    raise
                user += "\n上次结果未通过校验，请按结构约束重新输出，并逐个核对引用ID。"
    except Exception:
        degraded = True
        sections = _fallback(facts)
    finally:
        if client is not None and (close := getattr(client, "close", None)) is not None:
            close()

    return {
        "schema_version": VERSION,
        "degraded": degraded,
        "sections": sections.model_dump(),
        "facts": {ref: facts[ref] for point in sections.points() for ref in point.evidence_refs},
    }

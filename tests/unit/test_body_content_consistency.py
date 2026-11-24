import json
from dataclasses import replace
from itertools import repeat
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.content_consistency.calculator import (
    BodyContentConsistencyCalculator,
    evaluate,
)
from qa_eval.calculators.content_consistency.recall import Encoder, open_cache
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.contracts.calculator_results import CalculatorDecisionV1
from qa_eval.contracts.datasets import SampleRecordV2
from qa_eval.contracts.registry import CalculatorDescriptorV1


def sample(body, sample_id="sample", role="html"):
    return SampleRecordV2.model_validate(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(uuid4()),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "body",
                    "modality": "text",
                    "role": f"article.body.{role}",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": body, "format": role},
                }
            ],
            "source": {"source_artifact_id": str(uuid4())},
            "lineage": {"input_fingerprint": "sha256:" + "0" * 64},
        }
    )


@pytest.mark.parametrize(
    ("body", "label", "score"),
    [
        ("企业补贴金额为1万元。企业补贴金额为2万元。", 0, 0.6),
        ("企业补贴金额为1万元，企业补贴金额为2万元。", 0, 0.6),
        ("企业补贴金额为1万元。企业补贴金额为10000元。", 1, 1.0),
        ("企业补贴金额为1至2万元。企业补贴金额为1.5至3万元。", 1, 1.0),
        ("企业补贴金额少于1万元。企业补贴金额不少于10000元。", 0, 0.6),
        ("企业办理期限为5工作日。企业办理期限为5自然日。", "NOT_EVALUATED", None),
        ("企业办理期限为60分钟。企业办理期限为1小时。", 1, 1.0),
        ("企业申请截止日期为2025/01/02。企业申请截止日期为2025年1月3日。", 0, 0.6),
        ("企业申请截止日期为2025-01-02。企业申请截止日期为2025年1月2日。", 1, 1.0),
        ("企业申请截止日期晚于2025-01-02。企业申请截止日期早于2025-01-03。", 0, 0.6),
        ("企业应当提交材料。企业不得提交材料。", 0, 0.6),
        ("企业应当不提交材料。企业应当提交材料。", 0, 0.6),
        ("企业提交材料。企业不提交材料。", 1, 0.8),
        ("企业允许提交材料。企业不得提交材料。", 1, 0.8),
        (
            "企业不得提交材料。企业应当提交材料。企业应当提交材料。企业不得提交材料。",
            0,
            0.6,
        ),
        ("企业补贴金额为1万元。居民补贴金额为2万元。", "NOT_EVALUATED", None),
        (
            "若审核通过，企业补贴金额为1万元。若审核失败，企业补贴金额为2万元。",
            "NOT_EVALUATED",
            None,
        ),
        (
            "在2025年期间，企业补贴金额为1万元。 在2026年期间，企业补贴金额为2万元。",
            1,
            1.0,
        ),
        ("若审核通过，企业补贴金额为1万元。若审核通过，企业补贴金额为2万元。", 0, 0.6),
        (
            "企业补贴金额为1万元。除特殊企业外，企业补贴金额为2万元。",
            "NOT_EVALUATED",
            None,
        ),
        (
            "企业补贴金额为1万元。原标准已废止，企业补贴金额为2万元。",
            "NOT_EVALUATED",
            None,
        ),
        (
            "企业补贴金额为1万元。依据《旧办法》，企业补贴金额为2万元。",
            "NOT_EVALUATED",
            None,
        ),
        ("企业补贴金额为1万元。过渡期内，企业补贴金额为2万元。", "NOT_EVALUATED", None),
        ("本办法自2025年1月2日起施行。本办法自2026年1月2日起实施。", 0, 0.6),
        (
            "企业应当在收到通知后5日内提交材料。企业应当在收到通知后10日后提交材料。",
            0,
            0.6,
        ),
        (
            "企业应当在收到通知后5日内提交材料。企业应当在收到通知后10日内提交材料。",
            1,
            1.0,
        ),
        (
            "类型甲，企业补贴金额为1万元。类型乙，企业补贴金额为2万元。",
            "NOT_EVALUATED",
            None,
        ),
        ("企业补贴比例为50%以上。企业补贴比例低于50%。", 1, 0.8),
        (
            "企业申请截止时间为2025年1月2日17时。企业申请截止时间为2025-01-02 17:00。",
            1,
            1.0,
        ),
        ("企业申请截止时间为17时。企业申请截止时间为18:00。", 0, 0.6),
        ('<p> "null" </p>', "NOT_EVALUATED", None),
        ("<p>null</p>", "NOT_EVALUATED", None),
        (
            "<div>请查看<a href='https://example.org'>附件.pdf</a></div>",
            "NOT_EVALUATED",
            None,
        ),
        ("https://example.org/item", "NOT_EVALUATED", None),
        ("", "NOT_EVALUATED", None),
    ],
)
def test_rules(body, label, score):
    decision = CalculatorDecisionV1.model_validate(evaluate(sample(body), {}))
    assert decision.attributes["label"] == label, decision.attributes
    assert decision.attributes["score"] == score


def test_cross_chapter_long_and_source_span():
    body = (
        "<p>第一章 总则</p><p>第一条 企业补贴金额为1万元。</p>"
        + "<p>"
        + "普通正文内容" * 3000
        + "</p>"
        + "<p>第二章 附则</p><p>第二条 企业补贴金额为2万元。</p>"
    )
    row = evaluate(sample(body), {})
    [left, right] = row["locations"]
    assert left["attributes"]["clause_path"] == ["第一章", "第一条"]
    assert right["attributes"]["clause_path"] == ["第二章", "第二条"]
    assert body[right["start"] : right["end"]] == "第二条 企业补贴金额为2万元。"
    assert row["attributes"]["body_processing_complete"]


def test_table_and_formats():
    body = (
        "<table><thead><tr><th>对象</th><th><span>补贴金额</span></th></tr></thead><tbody>"
        "<tr><td>企业</td><td>1万元</td></tr><tr><td>企业</td><td>2万元</td></tr></tbody></table>"
    )
    row = evaluate(sample(body), {})
    assert row["status"] == "0"
    for location, value, row_no in zip(
        row["locations"], ("1万元", "2万元"), (2, 3), strict=True
    ):
        assert body[location["start"] : location["end"]] == value
        assert location["attributes"]["table_cell"][1:] == [row_no, 2]
    [pair] = row["attributes"]["pairs"]
    assert pair["left"]["table_context"][0]["table_cell"][1:] == [2, 1]
    assert (
        evaluate(sample(body.replace("<th><span>", "<th colspan='2'><span>")), {})[
            "status"
        ]
        == "NOT_EVALUATED"
    )
    plain = "\r\n 企业补贴金额为1万元。\n 企业补贴金额为2万元。"
    result = evaluate(sample(plain, role="plain"), {})
    assert result["status"] == "0"
    assert (
        plain[result["locations"][1]["start"] : result["locations"][1]["end"]]
        == " 企业补贴金额为2万元。"
    )


def test_dedup_and_threshold():
    body = (
        "企业补贴比例为20%。企业补贴比例为30%。企业补贴比例为30%。"
        "企业名额为1人。企业名额为2人。"
    )
    at_threshold = evaluate(sample(body), {})
    assert at_threshold["attributes"]["conflict_count"] == 2
    assert at_threshold["attributes"]["score"] == 0.7
    assert at_threshold["status"] == "1"
    assert evaluate(sample(body), {"classification_threshold": 0.7001})["status"] == "0"


def test_candidate_budget_and_cache(tmp_path):
    body = "。".join(repeat("企业补贴金额为1万元", 100)) + "。企业补贴金额为2万元。"
    with open_cache(tmp_path / "cache.sqlite") as connection:
        encoder = Encoder("concept", None, connection, 8)
        parameters = {"top_k": 2, "posting_limit": 4}
        rule = evaluate(sample(body), parameters)["attributes"]
        vector = evaluate(sample(body), parameters, encoder)["attributes"]
        assert vector["candidate_counts"]["vector_candidates"]
        assert rule["candidate_counts"]["candidates"] <= 2 * 101
        assert vector["candidate_counts"]["candidates"] <= 4 * 101
        assert vector["rule_truncated_queries"] and vector["vector_truncated_queries"]
        assert vector["label"] == rule["label"] == 0
        warm = Encoder("concept", None, connection, 8)
        assert evaluate(sample(body), parameters, warm)["attributes"] == vector
        assert warm.hits == 101 and warm.misses == 0


def test_stream_registry_and_recovery(tmp_path, monkeypatch):
    descriptor = CalculatorDescriptorV1.model_validate_json(
        Path(
            "configs/registry/calculators/content-consistency-v2.0.0.json"
        ).read_bytes()
    )
    calculator = TrustedCalculatorLoader().load(descriptor)
    assert isinstance(calculator, BodyContentConsistencyCalculator)
    source = tmp_path / "input.jsonl"
    sample_rows = [
        sample("企业补贴金额为1万元。企业补贴金额为2万元。", str(idx))
        for idx in range(110)
    ]
    valid_rows = "".join(item.model_dump_json() + "\n" for item in sample_rows)
    source.write_text(valid_rows + "invalid\n")
    context = CalculatorContext(
        uuid4(), uuid4(), uuid4(), "0206", "2.0.0", source, tmp_path / "output", "2.0"
    )
    monkeypatch.setenv("QA_EVAL_0206_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    with pytest.raises(CalculatorValidationError, match="invalid normalized JSONL"):
        calculator.execute(context, {})
    source.write_text(valid_rows + sample("null", "missing").model_dump_json() + "\n")
    result = calculator.execute(
        replace(context, run_id=uuid4(), stage_run_id=uuid4()), {}
    )
    assert result.status_detail["checkpoint_records_reused"] == 64
    assert result.statistics["input_count"] == 111
    assert result.statistics["evaluable_record_count"] == 110
    assert result.statistics["coverage"] == 110 / 111
    assert result.statistics["exclusions"] == {"PLACEHOLDER_BODY": 1}
    descriptor.native_output.validate_statistics(result.statistics)
    rows = [json.loads(line) for line in result.decisions.path.read_text().splitlines()]
    assert rows[-1]["status"] == "NOT_EVALUATED"


def test_html_priority_and_plain_fallback():
    html = sample("<p>企业补贴金额为1万元。</p><p>企业补贴金额为2万元。</p>")
    [plain] = sample(
        "企业补贴金额为1万元。企业补贴金额为10000元。", role="plain"
    ).contents
    plain = plain.model_copy(update={"content_id": "plain"})
    html.contents.append(plain)
    assert evaluate(html, {})["status"] == "0"
    empty_html = sample("<div> </div>")
    empty_html.contents.append(plain)
    row = evaluate(empty_html, {})
    assert row["status"] == "1"
    assert row["attributes"]["body_role"] == "article.body.plain"


def test_unconfigured_bge_fails_without_remote_fallback():
    with pytest.raises(CalculatorValidationError, match="local model directory"):
        Encoder("bge", None, None, 8)

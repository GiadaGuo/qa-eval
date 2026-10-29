"""02080106 文本纯净程度 Calculator 的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.text_purity import TextPurityCalculator


def _sample(
    version_id,
    sample_id: str,
    *,
    presence="PRESENT",
    text="正文内容",
    role="article.body.plain",
):
    content = {
        "content_id": "body",
        "modality": "text",
        "role": role,
        "presence": presence,
        "attributes": {},
    }
    if presence == "PRESENT":
        content["payload"] = {"type": "inline_text", "text": text, "format": "plain"}
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version_id),
        "sample_id": sample_id,
        "contents": [content],
        "annotations": [],
        "metadata": {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "locator": None,
            "attributes": {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records, name: str = "purity") -> CalculatorContext:
    path = tmp_path / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080106",
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / f"out-{name}",
        input_schema_ref="2.0",
    )


NORMAL = "我们坚持改革开放，不断完善社会主义市场经济体制。详见 http://example.com/policy 与 GitHub README。"
GARBLED = "锟斤拷锟斤拷改革意见全文补充通知"
HTML = '<p>正文</p><table class="x"><tr><td>数据</td></tr></table>&nbsp;'
AD = "想了解更多请加微信 kefu_2020 备注「资料」，限时特价！"


def test_execute_reports_deduplicated_record_counts(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "normal", text=NORMAL),
        _sample(version, "url-english", text=NORMAL),
        _sample(version, "garbled", text=GARBLED),
        _sample(version, "html", text=HTML),
        _sample(version, "ad", text=AD),
        # 同一条记录命中多条规则：HTML 残留 + 乱码 + 广告
        _sample(version, "multi", text=f"<p>锟斤拷</p><br>{AD}"),
        _sample(version, "empty", text="  \n"),
        _sample(version, "null", presence="NULL"),
    ]
    result = TextPurityCalculator().execute(_context(tmp_path, records), {})

    observed = {item["key"]: item["value"] for item in result.observations}
    # 4 条受影响（garbled/html/ad/multi；两条正常文本不受影响），分母排除空文本与 NULL
    assert observed["interference_record_count"] == 4
    assert observed["evaluable_record_count"] == 6
    assert observed["interference_record_ratio"] == 4 / 6
    # 命中次数之和大于受影响记录数：multi 记录命中多条规则
    assert observed["rule_hit_count"] > observed["interference_record_count"]
    ratio = next(
        item for item in result.observations if item["key"] == "interference_record_ratio"
    )
    assert ratio["numerator"] == 4
    assert ratio["denominator"] == 6

    assert result.population == {
        "input_count": 8,
        "evaluated_count": 6,
        "excluded_count": 2,
        "exclusions": [
            {"reason": "TEXT_EMPTY", "count": 1},
            {"reason": "TEXT_NULL", "count": 1},
        ],
    }


def test_execute_marks_partial_semantic_coverage(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "ad", text=AD)]
    result = TextPurityCalculator().execute(_context(tmp_path, records), {})

    coverage = result.semantic_coverage
    assert coverage["level"] == "PARTIAL"
    assert any("Shared LLM" in item for item in coverage["excluded"])
    assert any("PARTIAL" in item or "完整" in item for item in coverage["limitations"])


def test_execute_writes_hit_evidence_with_rule_metadata(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "multi", text=f"<p>锟斤拷</p><br>{AD}")]
    result = TextPurityCalculator().execute(_context(tmp_path, records), {})

    evidence = result.evidence[0]
    assert evidence.kind == "TEXT_PURITY_VIOLATIONS"
    assert evidence.record_count == 1
    assert evidence.redaction_policy == "matched-excerpts-only"
    rows = [json.loads(line) for line in evidence.path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["sample_id"] == "multi"
    hit_ids = {hit["rule_id"] for hit in row["hits"]}
    assert hit_ids == {
        "GARBLED_UNPRINTABLE_CHARS",
        "HTML_MARKUP_RESIDUE",
        "EXPLICIT_ADVERTISING_MARKER",
    }
    for hit in row["hits"]:
        assert hit["rule_version"] == "1.0.0"
        assert hit["detection_source"] == "RULE"
        assert hit["count"] >= 1
        assert hit["matches"]
    # 证据只含命中片段，不含整段原文
    assert all(len(match["excerpt"]) <= 60 for hit in row["hits"] for match in hit["matches"])
    assert NORMAL not in evidence.path.read_text()

    stats = result.diagnostics[-1]["rule_stats"]
    assert set(stats) == {
        "GARBLED_UNPRINTABLE_CHARS",
        "REPETITIVE_SYMBOL_PILEUP",
        "HTML_MARKUP_RESIDUE",
        "LAYOUT_PAGE_RESIDUE",
        "EXPLICIT_ADVERTISING_MARKER",
    }
    # 每条规则的 affected_record_count 与 hit_count 分开统计
    assert stats["HTML_MARKUP_RESIDUE"]["hit_count"] >= 2  # <p> 与 <br>
    assert stats["HTML_MARKUP_RESIDUE"]["affected_record_count"] == 1


def test_execute_supports_enabled_rules_and_missing_role_exclusion(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "ad", text=AD),
        _sample(version, "other-role", text=AD, role="article.summary"),
    ]
    result = TextPurityCalculator().execute(
        _context(tmp_path, records),
        {"enabled_rules": ["EXPLICIT_ADVERTISING_MARKER"]},
    )
    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed["interference_record_count"] == 1
    assert observed["evaluable_record_count"] == 1
    assert result.population["exclusions"] == [
        {"reason": "TEXT_ROLE_MISSING", "count": 1}
    ]


def test_execute_handles_all_invalid_records(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "empty", text="")]
    result = TextPurityCalculator().execute(_context(tmp_path, records), {})
    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed == {
        "interference_record_count": 0,
        "evaluable_record_count": 0,
        "rule_hit_count": 0,
    }


def test_validate_rejects_unknown_rules_bad_regex_and_schema(tmp_path: Path):
    calculator = TextPurityCalculator()
    context = _context(tmp_path, [], "validate")
    with pytest.raises(CalculatorValidationError, match="SampleRecord 2.0"):
        invalid = CalculatorContext(**{**context.__dict__, "input_schema_ref": "1.0"})
        calculator.validate(invalid, {})
    with pytest.raises(CalculatorValidationError, match="unknown rule"):
        calculator.validate(context, {"enabled_rules": ["NOT_A_RULE"]})
    with pytest.raises(CalculatorValidationError, match="invalid regular expression"):
        calculator.validate(context, {"ad_marker_patterns": ["(("]})
    with pytest.raises(CalculatorValidationError, match="non-empty list"):
        calculator.validate(context, {"enabled_rules": []})


def test_all_zero_output_stays_valid_metric_payload(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "normal", text=NORMAL)]
    result = TextPurityCalculator().execute(_context(tmp_path, records), {})
    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed["interference_record_ratio"] == 0.0
    assert result.evidence[0].record_count == 0
    assert result.evidence[0].path.read_text() == ""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators import government_html_format_compliance as module
from qa_eval.calculators.government_html_format_compliance import (
    GovernmentHtmlFormatComplianceCalculator,
)
from qa_eval.calculators.government_html_format_rules import (
    HtmlFormatParseError,
    build_dom,
    dom_path,
    length_to_px,
    parse_line_height,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError


def _content(
    content_id: str,
    role: str,
    text: str | None,
    *,
    fmt="html",
    modality="text",
    presence=None,
    source="$.content",
):
    presence = presence or ("PRESENT" if text is not None else "NULL")
    item = {
        "content_id": content_id,
        "modality": modality,
        "role": role,
        "presence": presence,
        "attributes": {"source_paths": [source], "transform": "identity"},
    }
    if presence == "PRESENT":
        if modality == "text":
            item["payload"] = {"type": "inline_text", "text": text, "format": fmt}
        else:
            item["payload"] = {
                "type": "structured",
                "data": {},
                "schema_ref": "test://schema",
            }
    return item


def _sample(version, sample_id, contents):
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version),
        "sample_id": sample_id,
        "contents": contents,
        "annotations": [],
        "metadata": {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "attributes": {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records):
    path = tmp_path / "samples.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in records),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="0201",
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / "out",
        input_schema_ref="2.0",
    )


def _observed(result):
    return {item["key"]: item["value"] for item in result.observations}


def _evidence(result):
    return [
        json.loads(line) for line in result.evidence[0].path.read_text().splitlines()
    ]


def _stats(result):
    return result.diagnostics[-1]


def _html_body(sample_id: str, html: str, version=None):
    return _sample(
        version or uuid4(),
        sample_id,
        [_content("body-html", "article.body.html", html)],
    )


CALCULATOR = GovernmentHtmlFormatComplianceCalculator()

CLEAN_GOVERNMENT_HTML = (
    '<div><h3 style="font-size:22px">关于印发某某办法的通知</h3>'
    '<p style="font-size:16px;font-family:仿宋">各市、县人民政府：'
    "现将《某某办法》印发给你们，请认真贯彻执行。</p>"
    '<p style="font-size:16px;line-height:28px;text-indent:2em;font-family:仿宋">'
    "第一条&nbsp;为规范管理制定本办法，详见"
    '<a href="https://www.example.gov.cn/policy/1">政策原文</a>。</p>'
    '<p style="text-align:right">二〇二四年三月一日</p></div>'
)


# ---------------------------------------------------------------------------
# 正常场景：不误报
# ---------------------------------------------------------------------------


def test_normal_government_html_passes(tmp_path: Path):
    result = CALCULATOR.execute(
        _context(tmp_path, [_html_body("ok", CLEAN_GOVERNMENT_HTML)]), {}
    )
    assert result.status == "OK"
    assert _observed(result)["compliant_record_ratio"] == 1.0
    assert _stats(result)["violation_count"] == 0
    assert _evidence(result) == []


def test_parent_child_font_size_override_and_short_runs_do_not_fire(tmp_path: Path):
    html = (
        '<div><p style="font-size:14px">'
        + "正常正文内容若干。" * 10
        + '<span style="font-size:21px">正常正文</span>'
        + '<span style="font-size:8px">注</span>'
        + "正文继续。" * 10
        + "</p></div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("ov", html)]), {})
    assert _evidence(result) == []


def test_uniform_fangsong_body_does_not_fire(tmp_path: Path):
    html = (
        "<div>"
        + '<p style="font-size:16px;font-family:仿宋">'
        + "仿宋正文第一段内容，全部使用仿宋字体。" * 5
        + "</p>"
        + '<p style="font-size:16px;font-family:仿宋,FangSong">仿宋正文第二段内容。</p>'
        + "</div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("uni", html)]), {})
    assert _evidence(result) == []


def test_headings_signature_and_links_use_different_sizes_without_hits(tmp_path):
    html = (
        "<div>"
        '<h1 style="font-size:32px;font-family:黑体">总标题</h1>'
        '<h2 style="font-size:24px">章节标题</h2>'
        '<p style="font-size:16px">正文段落内容较长，用于确立主流字号基线，避免把标题字号误判为离群值。</p>'
        '<p style="font-size:12px;text-align:right">发布日期：2024-03-01</p>'
        '<p style="font-size:14px">详见<a href="https://example.gov.cn/a">政策原文链接</a>。</p>'
        "</div>"
    )
    result = CALCULATOR.execute(
        _context(Path(tmp_path), [_html_body("mixed", html)]), {}
    )
    assert _evidence(result) == []


def test_justify_alignment_and_signature_right_align_do_not_fire(tmp_path):
    html = (
        "<div>"
        '<p style="text-align:justify">两端对齐是政务网页常见排版方式，'
        "不会触发任何异常判定逻辑。</p>"
        '<p style="text-align:right">某某市人民政府</p>'
        '<p style="text-align:right">二〇二四年三月一日</p>'
        "</div>"
    )
    result = CALCULATOR.execute(_context(Path(tmp_path), [_html_body("al", html)]), {})
    assert _evidence(result) == []


def test_normal_nbsp_br_and_single_empty_paragraph_do_not_fire(tmp_path):
    html = (
        "<div><p>第一段&nbsp;正文内容。</p><p>第二段正文内容。</p>"
        "<p><br></p><p></p><p>落款前留白后的正文。</p></div>"
    )
    result = CALCULATOR.execute(_context(Path(tmp_path), [_html_body("ws", html)]), {})
    assert _evidence(result) == []


def test_normal_table_and_legal_merged_cells_do_not_fire(tmp_path):
    html = (
        "<div><table>"
        "<tr><th>项目</th><th>金额</th></tr>"
        '<tr><td rowspan="2">合并两行</td><td>100</td></tr>'
        "<tr><td>200</td></tr>"
        '<tr><td colspan="2">合计300</td></tr>'
        "</table>"
        "<table><tr><td>嵌套外层</td><td><table><tr><td>嵌套内层</td></tr></table></td></tr></table>"
        "</div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("tbl", html)]), {})
    assert _evidence(result) == []
    assert _stats(result)["css_capability"]["tables_with_rowspan_skipped"] == 1


# ---------------------------------------------------------------------------
# 异常场景：各类违规
# ---------------------------------------------------------------------------


def test_misnested_tags_report_structure_anomaly(tmp_path: Path):
    html = "<div><p><b>加粗文本内容</p></b><p>第二段正常。</p></div>"
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("nest", html)]), {})
    rows = [
        row
        for row in _evidence(result)
        if row["violation_code"] == "HTML_STRUCTURE_ANOMALY"
    ]
    assert {(row["confidence"], row["observed_value"]) for row in rows} == {
        ("SUSPECTED", "</p> implicitly closed <b>"),
        ("CONFIRMED", "</b>"),
    }
    assert all(row["dom_path"] == "div[1]" for row in rows)


def test_stray_end_tag_and_unclosed_div_are_confirmed(tmp_path: Path):
    html = "<div><p>段落一。</p></span><p>段落二。</p>"
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("stray", html)]), {})
    rows = _evidence(result)
    assert {(row["observed_value"], row["confidence"]) for row in rows} == {
        ("</span>", "CONFIRMED"),
        ("<div> left open at end of document", "CONFIRMED"),
    }


def test_font_size_absolute_and_relative_outliers(tmp_path: Path):
    html = (
        "<div>"
        '<p style="font-size:16px">'
        + "正文基线内容，用于确立文档主流字号。"
        * 4
        + "</p>"
        '<p style="font-size:6px">这一段字号极小，用来触发绝对离群判定逻辑。</p>'
        '<p style="font-size:40px">这一段字号明显偏大，偏离正文主流字号基线。</p>'
        "</div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("font", html)]), {})
    rows = [
        row for row in _evidence(result) if row["violation_code"] == "FONT_SIZE_OUTLIER"
    ]
    assert {row["observed_value"] for row in rows} == {"6px", "40px"}
    assert all(row["expected_value"] for row in rows)
    by_size = {row["observed_value"]: row for row in rows}
    assert by_size["6px"]["dom_path"] == "div[1]/p[2]"
    assert by_size["40px"]["expected_value"].startswith("document body baseline")


def test_font_family_inconsistency(tmp_path: Path):
    html = (
        "<div>"
        '<p style="font-size:16px;font-family:仿宋">'
        + "仿宋正文段落内容，全部使用仿宋字体书写排版。"
        * 3
        + "</p>"
        '<p style="font-size:16px;font-family:Comic Sans MS">'
        "这一段使用完全不同的西文字体书写，明显偏离主流字体设置，字符数量充足。</p>"
        "</div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("fam", html)]), {})
    rows = [
        row
        for row in _evidence(result)
        if row["violation_code"] == "FONT_FAMILY_INCONSISTENCY"
    ]
    assert len(rows) == 1
    assert rows[0]["observed_value"] == "comic sans ms"
    assert rows[0]["confidence"] == "SUSPECTED"


def test_extreme_line_height_indent_and_negative_margin(tmp_path):
    html = (
        "<div>"
        '<p style="font-size:16px;line-height:320px">极端行高正文测试。</p>'
        '<p style="font-size:16px;text-indent:20em">极端缩进正文测试，内容长度足够触发判定逻辑。</p>'
        '<p style="font-size:16px;margin-top:-80px">负边距正文测试，内容长度足够触发判定逻辑。</p>'
        '<p style="font-size:16px;line-height:39px">三十九像素行高属于政府公文常见排版参数。</p>'
        '<p style="font-size:16px;text-indent:2em">两字符首行缩进属于政务正文常见排版。</p>'
        "</div>"
    )
    result = CALCULATOR.execute(
        _context(Path(tmp_path), [_html_body("spacing", html)]), {}
    )
    rows = _evidence(result)
    assert [row["violation_code"] for row in rows] == ["PARAGRAPH_SPACING_ANOMALY"] * 3
    assert rows[0]["dom_path"] == "div[1]/p[1]"
    assert "line-height:320px" in rows[0]["observed_value"]
    assert "text-indent:20em" in rows[1]["observed_value"]
    assert "margin-top:-80px" in rows[2]["observed_value"]


def test_consecutive_blank_paragraphs_and_br_runs(tmp_path):
    html = (
        "<div>"
        + "<p></p>" * 6
        + "<p>正文内容一段。</p>"
        + "<p><br><br><br><br><br></p>"
        + "<p>&nbsp;</p>"
        + "</div>"
    )
    result = CALCULATOR.execute(
        _context(Path(tmp_path), [_html_body("blank", html)]), {}
    )
    rows = [
        row
        for row in _evidence(result)
        if row["violation_code"] == "EXCESSIVE_EMPTY_BLOCKS"
    ]
    assert {(row["observed_value"], row["dom_path"]) for row in rows} == {
        ("6 consecutive blank blocks", "div[1]/p[1]"),
        ("5 consecutive <br>", "div[1]/p[8]/br[1]"),
    }
    assert all(row["confidence"] == "CONFIRMED" for row in rows)


def test_invalid_rowspan_colspan_and_table_hierarchy(tmp_path):
    html = (
        "<div>"
        "<table><tr><td>甲</td></tr><tr><td>乙</td><td>丙</td></tr></table>"
        '<table><tr><td colspan="0">坏列跨度</td></tr></table>'
        '<table><tr><td rowspan="x">坏行跨度</td></tr></table>'
        "<div><tr><td>游离行</td></tr></div>"
        "<table></table>"
        "</div>"
    )
    result = CALCULATOR.execute(_context(Path(tmp_path), [_html_body("tbl", html)]), {})
    rows = [
        row
        for row in _evidence(result)
        if row["violation_code"] == "TABLE_STRUCTURE_ANOMALY"
    ]
    observed = {row["observed_value"] for row in rows}
    assert observed == {
        'colspan="0"',
        'rowspan="x"',
        "<tr> outside <table>",
        "<td> outside <table>",
        "table without any cell",
        "row cell-span totals [1, 2]",
    }
    confident = {
        row["observed_value"] for row in rows if row["confidence"] == "CONFIRMED"
    }
    assert {'colspan="0"', 'rowspan="x"', "<tr> outside <table>"} <= confident


def test_nav_and_footer_dom_residue(tmp_path):
    html = (
        "<div>"
        "<p>正文一段。</p>"
        '<div class="breadcrumb current">当前位置：首页&nbsp;&gt;&nbsp;政策文件</div>'
        '<nav><a href="/a">导航一</a><a href="/b">导航二</a><a href="/c">导航三</a></nav>'
        '<div id="page-footer">版权所有 | 备案号ICP备12345</div>'
        "</div>"
    )
    result = CALCULATOR.execute(_context(Path(tmp_path), [_html_body("nav", html)]), {})
    rows = [
        row
        for row in _evidence(result)
        if row["violation_code"] == "HTML_LAYOUT_RESIDUE"
    ]
    assert len(rows) == 3
    assert all(row["confidence"] == "SUSPECTED" for row in rows)
    assert {row["dom_path"] for row in rows} == {
        "div[1]/div[1]",
        "div[1]/nav[1]",
        "div[1]/div[2]",
    }


def test_bare_nav_without_link_list_is_not_residue(tmp_path):
    html = "<div><p>正文一段。</p><nav>当前位置</nav><footer>联系地址：某市某区</footer></div>"
    result = CALCULATOR.execute(
        _context(Path(tmp_path), [_html_body("bare", html)]), {}
    )
    assert _evidence(result) == []


def test_record_with_multiple_violations_counted_once(tmp_path):
    html = (
        '<div class="TRS_Editor"><p style="font-size:16px">正文内容'
        + "一二三四五六七八九十" * 4
        + '</p><p style="font-size:6px">这一段字号极小触发异常检测。</p>'
        '<div class="page-footer">版权所有 备案号</div></span></div>'
    )
    result = CALCULATOR.execute(_context(Path(tmp_path), [_html_body("bad", html)]), {})
    stats = _stats(result)
    assert stats["affected_record_count"] == 1
    assert stats["violation_count"] >= 3
    assert stats["confirmed_violation_record_count"] == 1
    assert stats["suspected_anomaly_record_count"] == 1
    assert _observed(result)["non_compliant_record_count"] == 1
    # 证据定位到 sample 与 content
    rows = _evidence(result)
    assert all(
        row["sample_id"] == "bad" and row["content_id"] == "body-html" for row in rows
    )
    assert all(row["source_paths"] == ["$.content"] for row in rows)


# ---------------------------------------------------------------------------
# 边界场景
# ---------------------------------------------------------------------------


def test_missing_null_and_plain_only_records_are_excluded(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            "none",
            [
                _content("missing", "article.body.html", None, presence="MISSING"),
                _content("null", "article.body.html", None, presence="NULL"),
                _content("plain", "article.body.plain", "只有纯文本正文", fmt="plain"),
            ],
        )
    ]
    result = CALCULATOR.execute(_context(tmp_path, records), {})
    assert result.status == "NOT_APPLICABLE"
    assert _observed(result) == {}
    assert result.status_detail["reason_code"] == "NO_ELIGIBLE_HTML_CONTENT"
    assert result.population["excluded_count"] == 1
    assert result.population["exclusions"][0]["count"] == 1


def test_empty_html_record_is_excluded_as_empty_only(tmp_path):
    version = uuid4()
    records = [
        _sample(version, "empty", [_content("body-html", "article.body.html", "  \n")])
    ]
    result = CALCULATOR.execute(_context(tmp_path, records), {})
    assert result.status == "NOT_APPLICABLE"
    assert result.population["exclusions"] == [
        {"reason": "EMPTY_HTML_ONLY", "count": 1}
    ]
    assert _stats(result)["skipped_content_counts_by_reason"] == {"EMPTY_HTML": 1}


def test_oversized_html_is_skipped_and_counted(tmp_path):
    html = "<div>" + "x" * 1500 + "</div>"
    result = CALCULATOR.execute(
        _context(tmp_path, [_html_body("big", html)]),
        {"max_html_length_chars": 1000},
    )
    stats = _stats(result)
    assert stats["skipped_content_counts_by_reason"] == {"HTML_TOO_LARGE": 1}
    assert result.status == "NOT_APPLICABLE"
    assert result.population["exclusions"] == [
        {"reason": "HTML_CONTENT_NOT_SCANNABLE", "count": 1}
    ]


def test_unparseable_html_is_skipped_not_crashing(tmp_path, monkeypatch):
    def _explode(value):
        raise HtmlFormatParseError("boom")

    monkeypatch.setattr(module, "build_dom", _explode)
    result = CALCULATOR.execute(
        _context(tmp_path, [_html_body("bad", "<p>无论什么内容</p>")]), {}
    )
    stats = _stats(result)
    assert stats["skipped_content_counts_by_reason"] == {"HTML_PARSE_FAILED": 1}
    assert result.status == "NOT_APPLICABLE"


def test_multiple_html_contents_per_record(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            "multi",
            [
                _content("bad", "article.body.html", "<p>正文。</p></span>"),
                _content("good", "article.body.html", "<p>正常段落内容。</p>"),
                _content("fragment", "article.fragment.html", "<p>其他角色。</p>"),
            ],
        )
    ]
    result = CALCULATOR.execute(_context(tmp_path, records), {})
    stats = _stats(result)
    assert stats["scanned_content_count"] == 2
    assert stats["affected_record_count"] == 1
    assert stats["violation_count"] == 1
    rows = _evidence(result)
    assert len(rows) == 1 and rows[0]["content_id"] == "bad"
    assert stats["skipped_content_counts_by_reason"] == {"ROLE_NOT_INCLUDED": 1}


def test_entity_decoding_and_css_unit_conversion(tmp_path):
    html = (
        "<div>"
        '<p style="font-size:12pt;line-height:18pt;text-indent:2em">磅值单位正常换算，正文内容若干。</p>'
        '<p style="font-size:16px;line-height:10vw">视口单位无法换算，正文内容若干。</p>'
        + "<p>&nbsp;</p>" * 6
        + "</div>"
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("unit", html)]), {})
    stats = _stats(result)
    assert stats["css_capability"]["unresolved_css_length_count"] >= 1
    codes = {row["violation_code"] for row in _evidence(result)}
    # 12pt/18pt 均在正常范围，不产生行高异常；&nbsp; 空块按解码后判定
    assert "PARAGRAPH_SPACING_ANOMALY" not in codes
    assert "EXCESSIVE_EMPTY_BLOCKS" in codes


def test_external_style_dependency_is_reported_as_capability_limit(tmp_path):
    html = (
        '<div class="TRS_Editor"><p class="content">正文使用外部样式表排版，'
        "本身没有内联字号声明。</p>"
        '<p style="font-size:1.2em">相对字号缺少父级基准，无法换算。</p></div>'
    )
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("css", html)]), {})
    capability = _stats(result)["css_capability"]
    assert capability["stylesheet_dependent_element_count"] >= 1
    assert capability["undeclared_font_size_char_count"] > 0
    assert capability["unresolved_font_size_declaration_count"] >= 1
    # 无法确定最终样式时不判定违规
    assert _evidence(result) == []


def test_evidence_offset_points_at_start_tag(tmp_path):
    html = '<div><p style="font-size:16px">正文基线内容，用于确立主流字号。</p><p style="font-size:6px">字号过小的一段正文内容。</p></div>'
    result = CALCULATOR.execute(_context(tmp_path, [_html_body("off", html)]), {})
    row = next(
        row for row in _evidence(result) if row["violation_code"] == "FONT_SIZE_OUTLIER"
    )
    assert row["dom_path"] == "div[1]/p[2]"
    assert html[row["source_offset"] :].startswith("<p style")
    assert row["evidence_text"].startswith("<p style")
    assert row["coordinate_space"] == "source_html"
    assert "Unicode character index" in row["offset_semantics"]


def test_evidence_limit_caps_rows_not_counts(tmp_path):
    html = (
        "<div>"
        + "".join(
            '<p style="font-size:16px">正常正文段落内容。</p>'
            '<span style="font-size:2px">偏小字号</span>'
            for _ in range(10)
        )
        + "</div>"
    )
    result = CALCULATOR.execute(
        _context(tmp_path, [_html_body("lim", html)]), {"max_evidence_per_record": 3}
    )
    stats = _stats(result)
    assert len(_evidence(result)) == 3
    assert stats["violation_count"] == 10
    assert stats["emitted_evidence_count"] == 3


def test_compliance_basis_controls_main_ratio(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            "clean",
            [_content("body-html", "article.body.html", CLEAN_GOVERNMENT_HTML)],
        ),
        _sample(
            version,
            "suspected-only",
            [
                _content(
                    "body-html",
                    "article.body.html",
                    '<div><p>疑似异常记录，只有启发式告警。</p><div class="breadcrumb">位置</div></div>',
                )
            ],
        ),
    ]
    default = CALCULATOR.execute(_context(tmp_path, records), {})
    # 默认口径：疑似异常不计入主合规率
    assert _observed(default)["compliant_record_ratio"] == 1.0
    stats = _stats(default)
    assert stats["affected_record_count"] == 1
    assert stats["suspected_anomaly_record_count"] == 1
    assert stats["confirmed_violation_record_count"] == 0
    assert _observed(default)["non_compliant_record_count"] == 0

    strict = CALCULATOR.execute(
        _context(tmp_path, records), {"compliance_basis": "CONFIRMED_AND_SUSPECTED"}
    )
    assert _observed(strict)["compliant_record_ratio"] == 0.5
    assert _observed(strict)["non_compliant_record_count"] == 1
    assert (
        strict.evidence[0].attributes["compliance_basis"] == "CONFIRMED_AND_SUSPECTED"
    )


def test_selectors_and_check_toggles(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            "s",
            [_content("frag", "article.fragment.html", "<p>ok</p></span>")],
        )
    ]
    result = CALCULATOR.execute(
        _context(tmp_path, records), {"include_roles": ["article.fragment.html"]}
    )
    assert _stats(result)["scanned_content_count"] == 1
    assert _observed(result)["non_compliant_record_count"] == 1

    toggled = CALCULATOR.execute(
        _context(tmp_path, records),
        {"include_roles": ["article.fragment.html"], "enabled_checks": ["font"]},
    )
    assert _observed(toggled)["compliant_record_ratio"] == 1.0


def test_exclude_roles_skips_matching_contents(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            "s",
            [
                _content("a", "article.body.html", "<p>ok</p></span>"),
                _content("b", "article.appendix.html", "<p>ok</p></span>"),
            ],
        )
    ]
    result = CALCULATOR.execute(
        _context(tmp_path, records),
        {
            "include_roles": ["article.body.html", "article.appendix.html"],
            "exclude_roles": ["article.body.html"],
        },
    )
    stats = _stats(result)
    assert stats["scanned_content_count"] == 1
    rows = _evidence(result)
    assert len(rows) == 1 and rows[0]["content_id"] == "b"


def test_ratio_across_records_and_streaming_many_records(tmp_path):
    version = uuid4()
    records = [
        _sample(
            version,
            f"s-{index}",
            [_content("body-html", "article.body.html", CLEAN_GOVERNMENT_HTML)],
        )
        for index in range(200)
    ]
    records.append(
        _sample(
            version,
            "bad",
            [_content("body-html", "article.body.html", "<p>正文。</p></span>")],
        )
    )
    result = CALCULATOR.execute(_context(tmp_path, records), {})
    observed = _observed(result)
    assert observed["evaluable_record_count"] == 201
    assert observed["compliant_record_ratio"] == 200 / 201
    stats = _stats(result)
    assert stats["violation_counts_by_code"] == {"HTML_STRUCTURE_ANOMALY": 1}
    # 规则指纹与版本随结果输出
    assert stats["rule_version"] == "1.0.0"
    assert stats["rule_config_fingerprint"].startswith("sha256:")
    assert (
        result.evidence[0].attributes["rule_config_fingerprint"]
        == stats["rule_config_fingerprint"]
    )


def test_validation_rejects_invalid_parameters_and_schema(tmp_path):
    calculator = CALCULATOR
    context = _context(tmp_path, [])
    with pytest.raises(CalculatorValidationError, match="non-empty list"):
        calculator.validate(context, {"include_roles": [""]})
    with pytest.raises(CalculatorValidationError, match="unknown check keys"):
        calculator.validate(context, {"enabled_checks": ["structure"]})
    with pytest.raises(CalculatorValidationError, match="CONFIRMED_ONLY"):
        calculator.validate(context, {"compliance_basis": "ALL"})
    with pytest.raises(CalculatorValidationError, match="at least 1"):
        calculator.validate(context, {"font_size_min_px": 0.5})
    with pytest.raises(CalculatorValidationError, match="integer"):
        calculator.validate(context, {"max_evidence_per_record": 1.5})
    with pytest.raises(CalculatorValidationError, match="below font_size_max_px"):
        calculator.validate(context, {"font_size_min_px": 80})
    with pytest.raises(CalculatorValidationError, match="invalid regular expression"):
        calculator.validate(context, {"layout_residue_patterns": ["("]})
    with pytest.raises(CalculatorValidationError, match="SampleRecord 2.0"):
        calculator.validate(
            CalculatorContext(**{**context.__dict__, "input_schema_ref": "1.0"}), {}
        )
    with pytest.raises(CalculatorValidationError, match="must be greater than 1"):
        calculator.validate(context, {"font_size_outlier_scale": 1})


def test_invalid_sample_record_raises_validation_error(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text('{"schema_version": "2.0"}\n', encoding="utf-8")
    context = CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="0201",
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / "out",
        input_schema_ref="2.0",
    )
    with pytest.raises(CalculatorValidationError, match="invalid SampleRecord"):
        CALCULATOR.execute(context, {})


# ---------------------------------------------------------------------------
# 共享规则模块与注册
# ---------------------------------------------------------------------------


def test_rules_dom_paths_and_unit_helpers():
    root, events = build_dom("<div><p>甲</p><p>乙<span>丙</span></p></div>")
    assert [event.kind for event in events] == []
    div = root.children[0]
    span = div.children[1].children[1]
    assert dom_path(span) == "div[1]/p[2]/span[1]"
    assert length_to_px("12pt", reference_px=None) == pytest.approx(16.0)
    assert length_to_px("2em", reference_px=16.0) == pytest.approx(32.0)
    assert length_to_px("10vw", reference_px=16.0) is None
    assert parse_line_height("1.5", font_px=20.0) == pytest.approx(30.0)
    assert parse_line_height("1.5", font_px=None) is None


def test_calculator_identity_matches_descriptor_and_loader():
    loader = TrustedCalculatorLoader()
    assert CALCULATOR.calculator_key == "government_html_format_compliance"
    assert CALCULATOR.version == "1.0.0"
    # source_paths 全部位于受信目录内且可读，digest 可计算
    digest = loader.code_digest(CALCULATOR)
    assert digest.startswith("sha256:")

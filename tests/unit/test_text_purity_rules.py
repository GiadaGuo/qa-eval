"""02080106 文本纯净程度规则层的单元测试。"""

from __future__ import annotations

import pytest

from qa_eval.calculators.text_purity_rules import (
    RULESET_VERSION,
    PurityRuleConfig,
    build_rules,
    detect_all,
    rule_catalog,
)

RULES = build_rules(PurityRuleConfig())


def _hit(rule_id: str, text: str):
    rule = next(item for item in RULES if item.rule_id == rule_id)
    return rule.detect(text)


def test_ruleset_identity_is_stable() -> None:
    catalog = rule_catalog()
    ids = [item["rule_id"] for item in catalog]
    assert ids == [
        "GARBLED_UNPRINTABLE_CHARS",
        "REPETITIVE_SYMBOL_PILEUP",
        "HTML_MARKUP_RESIDUE",
        "LAYOUT_PAGE_RESIDUE",
        "EXPLICIT_ADVERTISING_MARKER",
    ]
    assert all(item["rule_version"] == "1.0.0" for item in catalog)
    assert all(item["description"] for item in catalog)
    assert RULESET_VERSION == "1.0.0"


def test_garbled_chars_rule_hits_mojibake_and_replacement_chars() -> None:
    hit = _hit("GARBLED_UNPRINTABLE_CHARS", "锟斤拷锟斤拷教育改革意见全文")
    assert hit is not None
    assert hit.count >= 2
    assert hit.attributes["mojibake_sequence_count"] >= 2
    assert hit.matches[0].excerpt.startswith("锟斤拷")

    hit = _hit("GARBLED_UNPRINTABLE_CHARS", "会议指出要深化改革的�政策")
    assert hit is not None
    assert hit.attributes["garbled_char_count"] >= 1

    # 零宽字符（Cf 类别）
    hit = _hit("GARBLED_UNPRINTABLE_CHARS", "​会议指出深化改革开放")
    assert hit is not None


def test_garbled_chars_rule_ignores_normal_text() -> None:
    text = (
        "党的十一届三中全会以来，我们坚持改革开放，不断完善社会主义市场经济体制。"
        "2023 年第 4 号文件明确：GDP 增长 5.5%（详见 http://example.com/a?b=1）。"
    )
    assert _hit("GARBLED_UNPRINTABLE_CHARS", text) is None


def test_symbol_pileup_rule_hits_repetitive_runs_and_density() -> None:
    hit = _hit("REPETITIVE_SYMBOL_PILEUP", "重要通知!!!!!!请查收")
    assert hit is not None
    assert hit.attributes["symbol_run_count"] == 1
    assert hit.attributes["max_symbol_run_length"] == 6

    # 非 ASCII 装饰符号密度异常（≥10 个且密度 ≥5%）
    hit = _hit(
        "REPETITIVE_SYMBOL_PILEUP",
        "议程★安排★如下★上午★开幕★下午★闭幕★晚上★联欢★合影★",
    )
    assert hit is not None
    assert hit.attributes["decorative_symbol_count"] >= 10
    assert hit.attributes["decorative_symbol_density"] >= 0.05


def test_symbol_pileup_rule_ignores_legitimate_typography() -> None:
    # 省略号/连字符/破折号/等号线/代码符号/普通标点不属于堆积
    for text in (
        "他说……然后离开了。",
        "连载（一）——改革篇",
        "------ 分隔线 ------",
        "========================",
        "const x = a < b && c > d;",
        "第一，……第二，……第三，……",
    ):
        assert _hit("REPETITIVE_SYMBOL_PILEUP", text) is None, text


def test_html_residue_rule_counts_tags_and_entities() -> None:
    text = '<p>正文</p>\n<table class="x"><tr><td>数据</td></tr></table>\n&nbsp;'
    hit = _hit("HTML_MARKUP_RESIDUE", text)
    assert hit is not None
    assert hit.attributes["html_tag_count"] == 8
    assert hit.attributes["html_entity_count"] == 1
    assert "<table>" in hit.attributes["distinct_tag_counts"]
    assert any("<" in match.excerpt for match in hit.matches)


def test_html_residue_rule_ignores_code_snippets_below_threshold() -> None:
    # 技术文章中两三个标签（代码示例）不应命中默认阈值 3
    text = '在 React 中使用 <div className="app">Hello</div> 渲染组件。'
    assert _hit("HTML_MARKUP_RESIDUE", text) is None


def test_layout_residue_rule_hits_page_markers_and_footer() -> None:
    for text, expect in (
        ("……正文内容……\n第 3 页\n继续正文", "第 3 页"),
        ("……正文内容……\n第 2 页，共 12 页\n继续正文", "共 12 页"),
        ("……正文内容……\n共 12 页\n继续正文", "共 12 页"),
        ("……正文内容……\n- 12 -\n继续正文", "- 12 -"),
        ("……正文内容……\n版权所有某某门户网站 保留所有权利", "版权所有"),
        ("……正文内容……\n返回列表", "返回列表"),
    ):
        hit = _hit("LAYOUT_PAGE_RESIDUE", text)
        assert hit is not None, text
        assert expect in hit.matches[0].excerpt or any(
            expect in match.excerpt for match in hit.matches
        )


def test_layout_residue_rule_ignores_normal_references() -> None:
    # 正常编号/引用/日期不应命中；句中文献页码（如「《文选》第79页」）
    # 属于引用内容而非排版残留，只有独占一行的页码才算残留。
    for text in (
        "详见 2023 年第 4 号文件第 12 条。",
        "正如《邓小平文选》第79页所指出的，改革是发展的动力。",
        "该文收录在第 3 卷第 279 页起至第 305 页止的部分。",
        "Group 12 of the report covers policy.",
        "十四五规划纲要发布",
    ):
        assert _hit("LAYOUT_PAGE_RESIDUE", text) is None, text


def test_ad_marker_rule_hits_action_oriented_promotions() -> None:
    for text in (
        "想了解更多请加微信 kefu_2020 备注「资料」",
        "扫码关注公众号领取免费课程",
        "限时特价，全场五折起",
        "咨询热线：400-1234567",
        "点击下方链接立即购买",
    ):
        assert _hit("EXPLICIT_ADVERTISING_MARKER", text) is not None, text


def test_ad_marker_rule_ignores_news_discussion_of_ads() -> None:
    # 词面出现「广告/微信/推广」但非行动导向推广时不命中
    for text in (
        "报告显示我国广告行业年收入持续增长。",
        "微信是一款常用的即时通讯工具。",
        "会议指出要推广节能减排技术。",
        "他负责公司的推广部门工作。",
    ):
        assert _hit("EXPLICIT_ADVERTISING_MARKER", text) is None, text


def test_url_and_english_terms_are_not_flags() -> None:
    text = (
        "More info at https://example.com/post?id=12 & the GitHub README "
        "(v2.3.1, 2024-01-15). Copyright 法律法规另有规定的除外。"
    )
    assert detect_all(text, RULES) == []


def test_multiple_rules_fire_on_the_same_text() -> None:
    text = "<p>锟斤拷</p><br>\n第 1 页\n咨询热线：400-1234567"
    hits = detect_all(text, RULES)
    assert {hit.rule_id for hit in hits} >= {
        "GARBLED_UNPRINTABLE_CHARS",
        "HTML_MARKUP_RESIDUE",
        "LAYOUT_PAGE_RESIDUE",
        "EXPLICIT_ADVERTISING_MARKER",
    }


def test_detection_is_deterministic_and_versioned() -> None:
    text = "<p>锟斤拷</p> 加微信 abc123 领取优惠券"
    first = detect_all(text, RULES)
    second = detect_all(text, build_rules(PurityRuleConfig()))
    assert [hit.to_evidence_json() for hit in first] == [
        hit.to_evidence_json() for hit in second
    ]
    for hit in first:
        payload = hit.to_evidence_json()
        assert payload["detection_source"] == "RULE"
        assert payload["rule_version"] == "1.0.0"
        assert payload["count"] >= 1
        assert payload["matches"]


def test_custom_parameters_change_behavior() -> None:
    # 长文本中的单个替换符：比例低于默认阈值 1% 不命中，归零后命中
    normal = "会议指出要深化改革开放完善市场经济体制" * 6
    assert _hit("GARBLED_UNPRINTABLE_CHARS", normal + "�") is None
    strict = build_rules(PurityRuleConfig(garbled_char_ratio_threshold=0.0))
    rule = next(item for item in strict if item.rule_id == "GARBLED_UNPRINTABLE_CHARS")
    assert rule.detect(normal + "�") is not None

    custom = build_rules(PurityRuleConfig(ad_marker_patterns=(r"内部编号[::]\d+",)))
    rule = next(item for item in custom if item.rule_id == "EXPLICIT_ADVERTISING_MARKER")
    assert rule.detect("加微信 kefu_2020") is None
    assert rule.detect("内部编号:1024") is not None


@pytest.mark.parametrize(
    "threshold,expected_hits",
    [(3, 1), (10, 0)],
)
def test_html_threshold_parameter_changes_behavior(threshold, expected_hits) -> None:
    text = "使用 <b>加粗</b> 与 <i>斜体</i> 标签。"
    rules = build_rules(PurityRuleConfig(html_tag_count_threshold=threshold))
    rule = next(item for item in rules if item.rule_id == "HTML_MARKUP_RESIDUE")
    hit = rule.detect(text)
    assert (hit is not None) is bool(expected_hits)

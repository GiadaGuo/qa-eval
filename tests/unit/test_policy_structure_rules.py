import html

from qa_eval.calculators.policy_structure_compliance.html_source_map import (
    extract_html_blocks,
    validate_location,
)
from qa_eval.calculators.policy_structure_compliance.parsers import (
    number_value,
    parse_structure,
)


def _raw_text(source: str, spans) -> str:
    return "".join(source[item.start : item.end] for item in spans)


def test_source_map_entity_nested_cross_tag_and_duplicate_text() -> None:
    source = "<div><p>第一<span>条</span>&nbsp;总则</p><p>第一条 总则</p></div>"
    normalized, blocks, malformed = extract_html_blocks(source)
    assert not malformed
    assert [block.text for block in blocks] == ["第一条 总则", "第一条 总则"]
    assert normalized.count("第一条 总则") == 2
    first, second = blocks
    assert first.location.coordinate_status == "EXACT"
    assert len(first.location.source_html_spans) >= 3
    assert first.location.source_html_start != second.location.source_html_start
    assert "第一" in _raw_text(source, first.location.source_html_spans)
    assert "&nbsp;" in _raw_text(source, first.location.source_html_spans)
    assert validate_location(source, first.location)


def test_unicode_offsets_are_code_points_not_utf8_bytes() -> None:
    source = "<div>😀</div><p>第一条</p>"
    _normalized, blocks, _malformed = extract_html_blocks(source)
    offset = source.index("第一条")
    assert blocks[1].location.source_html_spans[0].start == offset
    assert source[offset : offset + 3] == "第一条"
    assert len(source[:offset].encode()) > offset


def test_malformed_html_degrades_to_block_only() -> None:
    source = "<div><p>第一条 内容</div>"
    _normalized, blocks, malformed = extract_html_blocks(source)
    assert malformed
    assert blocks[0].location.coordinate_status == "BLOCK_ONLY"
    assert validate_location(source, blocks[0].location)


def test_notification_recognition_prefers_metadata_and_body_selects_mode() -> None:
    result = parse_structure(
        html_source="<p>第一条 总则</p><p>第二条 职责</p>",
        plain_source=None,
        title="关于开展工作的文件",
        metadata={"extra": {"policyType": "通知"}},
    )
    assert result.document_type == "通知"
    assert result.document_type_source == "METADATA"
    assert result.document_type_confidence == 0.9
    assert result.mode == "LEGAL_HIERARCHY"
    assert [node.ordinal for node in result.nodes] == [1, 2]


def test_notification_outline_embedded_legal_and_unnumbered() -> None:
    outline = parse_structure(
        html_source="<p>一、总体要求</p><p>（一）主要任务</p>",
        plain_source=None,
        title="工作通知",
        metadata={},
    )
    mixed = parse_structure(
        html_source="<p>一、工作安排</p><p>第一条 适用范围</p>",
        plain_source=None,
        title="实施通知",
        metadata={},
    )
    generic = parse_structure(
        html_source="<p>现将有关事项通知如下。</p>",
        plain_source=None,
        title="普通通知",
        metadata={},
    )
    assert (outline.document_type, outline.mode) == ("通知", "OUTLINE_HIERARCHY")
    assert mixed.mode == "MIXED"
    assert generic.mode == "GENERIC_DOCUMENT"


def test_article_reference_inside_body_is_not_a_new_node() -> None:
    result = parse_structure(
        html_source="<p>一、要求</p><p>根据本办法第三条执行。</p>",
        plain_source=None,
        title="通知",
        metadata={},
    )
    assert len(result.nodes) == 1
    assert result.nodes[0].node_type == "OUTLINE_L1"


def test_no_chapter_measure_and_scoped_outline() -> None:
    result = parse_structure(
        html_source="<p>第一条 总则</p><p>第二条 管理</p>",
        plain_source=None,
        title="管理办法",
        metadata={},
    )
    assert result.document_type == "办法"
    assert result.mode == "LEGAL_HIERARCHY"
    assert all(node.parent_id is None for node in result.nodes)


def test_chinese_numbers() -> None:
    assert number_value("十") == 10
    assert number_value("二十三") == 23
    assert number_value("一百零二") == 102
    assert number_value("12") == 12


def test_entity_round_trip_decodes_visible_text() -> None:
    source = "<p>第一条&nbsp;职责&amp;范围</p>"
    _normalized, blocks, _malformed = extract_html_blocks(source)
    raw = _raw_text(source, blocks[0].location.source_html_spans)
    assert html.unescape(raw.replace("</span><span>", "")) == "第一条\xa0职责&范围"

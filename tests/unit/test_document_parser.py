# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# Markdown 解析器单测：行号化、标题结构、哈希与非法输入。
# ====================================================

import hashlib

import pytest

from qa_eval.application.document_evaluation.parser import (
    PARSER_VERSION,
    parse_markdown,
)

CONTENT = (
    "# 数据集说明\n"
    "\n"
    "本数据集包含 1,000 条记录。\n"
    "## 数据规模\n"
    "### 详细字段\n"
    "####### 非标题（7 个 #）\n"
    "#hashtag 非标题\n"
).encode()


def test_parse_line_count_sha_and_size():
    parsed = parse_markdown(CONTENT)

    assert parsed.line_count == 7
    assert parsed.size_bytes == len(CONTENT)
    assert parsed.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert parsed.lines[0] == "# 数据集说明"
    assert parsed.lines[2] == "本数据集包含 1,000 条记录。"


def test_parse_extracts_atx_heading_structure():
    parsed = parse_markdown(CONTENT)

    assert [(h.line, h.level, h.title) for h in parsed.headings] == [
        (1, 1, "数据集说明"),
        (4, 2, "数据规模"),
        (5, 3, "详细字段"),
    ]


def test_parse_rejects_non_utf8_and_empty():
    with pytest.raises(ValueError, match="UTF-8"):
        parse_markdown(b"# \xff\xfe")

    with pytest.raises(ValueError, match="空"):
        parse_markdown(b"   \n \n")

    with pytest.raises(ValueError, match="空"):
        parse_markdown(b"")


def test_render_numbered_and_artifact_roundtrip():
    parsed = parse_markdown(CONTENT)

    numbered = parsed.render_numbered()
    assert numbered.splitlines()[0] == "1|# 数据集说明"
    assert numbered.count("\n") == parsed.line_count - 1

    truncated = parsed.render_numbered(3)
    assert truncated.splitlines() == [
        "1|# 数据集说明",
        "2|",
        "3|本数据集包含 1,000 条记录。",
    ]

    artifact = parsed.to_artifact_document()
    assert artifact["schema_version"] == "1.0"
    assert artifact["parser"] == PARSER_VERSION
    assert artifact["document"]["line_count"] == parsed.line_count
    assert artifact["lines"] == list(parsed.lines)
    assert len(artifact["headings"]) == 3

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# Markdown 说明文档解析器（移植自 Skill 的 inspect_markdown.py）：
# 行号化、标题结构、行数、sha256。只做确定性解析，不做语义判定。
# ====================================================

from __future__ import annotations

import hashlib
from dataclasses import dataclass

PARSER_VERSION = "markdown_lines_v1"


@dataclass(frozen=True)
class DocumentHeading:
    line: int  # 1-based
    level: int
    title: str


@dataclass(frozen=True)
class ParsedDocument:
    sha256: str
    size_bytes: int
    line_count: int
    lines: tuple[str, ...]
    headings: tuple[DocumentHeading, ...]

    def render_numbered(self, last_line: int | None = None) -> str:
        """渲染「行号|内容」文本，供 LLM prompt 引用行号。"""

        end = self.line_count if last_line is None else min(last_line, self.line_count)
        return "\n".join(
            f"{number}|{self.lines[number - 1]}" for number in range(1, end + 1)
        )

    def to_artifact_document(self) -> dict:
        """行号化解析产物的持久化格式。"""

        return {
            "schema_version": "1.0",
            "parser": PARSER_VERSION,
            "document": {
                "sha256": self.sha256,
                "size_bytes": self.size_bytes,
                "line_count": self.line_count,
            },
            "headings": [
                {
                    "line": heading.line,
                    "level": heading.level,
                    "title": heading.title,
                }
                for heading in self.headings
            ],
            "lines": list(self.lines),
        }


def parse_markdown(content: bytes) -> ParsedDocument:
    """UTF-8 解码、行号化与标题结构提取；空文档抛 ValueError。"""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"说明文档不是有效 UTF-8：{exc}") from exc

    if not text.strip():
        raise ValueError("说明文档内容为空")

    lines = tuple(text.splitlines())
    headings: list[DocumentHeading] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        marker = stripped.split(maxsplit=1)[0]
        if marker and set(marker) == {"#"} and len(marker) <= 6:
            title = stripped[len(marker) :].strip()
            if title:
                headings.append(
                    DocumentHeading(line=number, level=len(marker), title=title)
                )

    return ParsedDocument(
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        line_count=len(lines),
        lines=lines,
        headings=tuple(headings),
    )

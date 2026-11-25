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


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    text: str
    locator: dict


@dataclass(frozen=True)
class BlockDocument(ParsedDocument):
    blocks: tuple[DocumentBlock, ...]


def parse_document(content: bytes, suffix: str) -> BlockDocument:
    """Number extracted blocks for the evaluator and preserve source coordinates."""
    import io
    import subprocess
    import zipfile
    from xml.etree import ElementTree

    if suffix == ".md":
        markdown = parse_markdown(content)
        located = [
            (line, {"line": index}) for index, line in enumerate(markdown.lines, 1)
        ]
    elif suffix == ".pdf":
        try:
            raw = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
                input=content,
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout.decode("utf-8")
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise ValueError("PDF 文本提取失败（需要 pdftotext）") from exc
        pages = raw.removesuffix("\f").split("\f")
        if any(not page.strip() for page in pages):
            raise ValueError("PDF 存在无可提取文本的页面")
        located = [
            (line, {"page": number, "line_on_page": index})
            for number, page in enumerate(pages, 1)
            for index, line in enumerate(page.splitlines(), 1)
        ]
    elif suffix == ".docx":
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                with archive.open("word/document.xml") as stream:
                    xml = stream.read(16_000_001)
            if len(xml) > 16_000_000 or b"<!DOCTYPE" in xml.upper():
                raise ValueError("DOCX XML 超出解析限制")
            body = ElementTree.fromstring(xml).find(ns + "body")
        except (
            zipfile.BadZipFile,
            KeyError,
            ElementTree.ParseError,
            RuntimeError,
        ) as exc:
            raise ValueError("DOCX 正文解析失败") from exc
        if body is None:
            raise ValueError("DOCX 缺少正文")

        def paragraphs(parent, locator):
            for index, p in enumerate(parent.findall(ns + "p"), 1):
                parts = []
                for node in p.iter():
                    if node.tag == ns + "t":
                        parts.append(node.text or "")
                    elif node.tag in {ns + "tab", ns + "br", ns + "cr"}:
                        parts.append(" ")
                yield "".join(parts), {**locator, "paragraph": index}

        def blocks(parent, locator):
            yield from paragraphs(parent, locator)
            for table, tbl in enumerate(parent.findall(ns + "tbl"), 1):
                for row, tr in enumerate(tbl.findall(ns + "tr"), 1):
                    for cell, tc in enumerate(tr.findall(ns + "tc"), 1):
                        yield from blocks(
                            tc,
                            {
                                **locator,
                                "table_path": [
                                    *locator.get("table_path", []),
                                    [table, row, cell],
                                ],
                            },
                        )

        # Iterate body elements in document order.
        located = []
        paragraph_index = table_index = 0
        for element in body:
            if element.tag == ns + "p":
                paragraph_index += 1
                wrapper = ElementTree.Element("wrapper")
                wrapper.append(element)
                line, _ = next(paragraphs(wrapper, {}))
                located.append((line, {"paragraph": paragraph_index}))
            elif element.tag == ns + "tbl":
                table_index += 1
                for row, tr in enumerate(element.findall(ns + "tr"), 1):
                    for cell, tc in enumerate(tr.findall(ns + "tc"), 1):
                        located.extend(
                            blocks(tc, {"table_path": [[table_index, row, cell]]})
                        )
    else:
        raise ValueError("说明文档须为 MD、PDF 或 DOCX")

    if not any(line.strip() for line, _ in located):
        raise ValueError("说明文档没有可提取文本")
    sha256 = hashlib.sha256(content).hexdigest()
    return BlockDocument(
        sha256,
        len(content),
        len(located),
        tuple(line for line, _ in located),
        (),
        tuple(
            DocumentBlock(f"{sha256}:{i}", line, locator)
            for i, (line, locator) in enumerate(located, 1)
        ),
    )

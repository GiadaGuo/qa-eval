"""Lexical HTML-to-text mapping with verifiable Unicode source coordinates."""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser

from qa_eval.calculators.policy_structure_compliance.schema import (
    SourceLocation,
    SourceSpan,
    StructureBlock,
)

BLOCK_TAGS = {"p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4", "br"}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "wbr"}


def _line_starts(value: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(value) if char == "\n")
    return starts


@dataclass(frozen=True)
class _MappedChar:
    value: str
    start: int
    end: int
    dom_path: str


class _LexicalMapParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = _line_starts(source)
        self.stack: list[tuple[str, int, str]] = []
        self.sibling_counts: list[Counter[str]] = [Counter()]
        self.chars: list[_MappedChar] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _path(self) -> str:
        return "/" + "/".join(item[2] for item in self.stack) if self.stack else "/"

    def _break(self, offset: int) -> None:
        if self.chars and self.chars[-1].value != "\n":
            self.chars.append(_MappedChar("\n", offset, offset, self._path()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        offset = self._offset()
        if tag in BLOCK_TAGS:
            self._break(offset)
        self.sibling_counts[-1][tag] += 1
        component = f"{tag}[{self.sibling_counts[-1][tag]}]"
        if tag not in VOID_TAGS:
            self.stack.append((tag, offset, component))
            self.sibling_counts.append(Counter())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        offset = self._offset()
        if tag in BLOCK_TAGS:
            self._break(offset)
        positions = [index for index, item in enumerate(self.stack) if item[0] == tag]
        if not positions:
            self.malformed = True
            return
        index = positions[-1]
        if index != len(self.stack) - 1:
            self.malformed = True
        del self.stack[index:]
        del self.sibling_counts[index + 1 :]

    def handle_data(self, data: str) -> None:
        offset = self._offset()
        for index, value in enumerate(data):
            self.chars.append(
                _MappedChar(value, offset + index, offset + index + 1, self._path())
            )

    def handle_entityref(self, name: str) -> None:
        offset = self._offset()
        raw = f"&{name};"
        for value in html.unescape(raw):
            self.chars.append(
                _MappedChar(value, offset, offset + len(raw), self._path())
            )

    def handle_charref(self, name: str) -> None:
        offset = self._offset()
        raw = f"&#{name};"
        for value in html.unescape(raw):
            self.chars.append(
                _MappedChar(value, offset, offset + len(raw), self._path())
            )


def _normalize(chars: list[_MappedChar]) -> list[_MappedChar]:
    result: list[_MappedChar] = []
    pending: _MappedChar | None = None
    for item in chars:
        if item.value.isspace():
            if item.value == "\n":
                pending = _MappedChar("\n", item.start, item.end, item.dom_path)
            elif pending is None:
                pending = _MappedChar(" ", item.start, item.end, item.dom_path)
            continue
        if pending is not None and result and result[-1].value != "\n":
            result.append(pending)
        pending = None
        result.append(item)
    while result and result[-1].value.isspace():
        result.pop()
    return result


def _spans(chars: list[_MappedChar]) -> list[SourceSpan]:
    spans: list[SourceSpan] = []
    for item in chars:
        if item.start == item.end:
            continue
        if spans and spans[-1].end == item.start:
            spans[-1] = SourceSpan(spans[-1].start, item.end)
        elif not spans or (spans[-1].start, spans[-1].end) != (item.start, item.end):
            spans.append(SourceSpan(item.start, item.end))
    return spans


def _common_dom_path(paths: set[str]) -> str | None:
    if not paths:
        return None
    split = [path.strip("/").split("/") for path in paths]
    common: list[str] = []
    for values in zip(*split, strict=False):
        if len(set(values)) != 1:
            break
        common.append(values[0])
    return "/" + "/".join(common) if common else "/"


def _location(
    chars: list[_MappedChar], start: int, end: int, malformed: bool
) -> SourceLocation:
    relevant = chars[start:end]
    spans = _spans(relevant)
    paths = {item.dom_path for item in relevant if item.start != item.end}
    return SourceLocation(
        normalized_text_start=start,
        normalized_text_end=end,
        source_html_start=spans[0].start if spans else None,
        source_html_end=spans[-1].end if spans else None,
        source_html_spans=spans,
        dom_path=next(iter(paths)) if len(paths) == 1 else _common_dom_path(paths),
        coordinate_status="BLOCK_ONLY" if malformed else "EXACT",
        reason="MALFORMED_HTML" if malformed else None,
    )


def extract_html_blocks(source: str) -> tuple[str, list[StructureBlock], bool]:
    parser = _LexicalMapParser(source)
    try:
        parser.feed(source)
        parser.close()
    except (AssertionError, ValueError):
        parser.malformed = True
    if parser.stack:
        parser.malformed = True
    chars = _normalize(parser.chars)
    normalized = "".join(item.value for item in chars)
    blocks: list[StructureBlock] = []
    start = 0
    for index in range(len(chars) + 1):
        if index < len(chars) and chars[index].value != "\n":
            continue
        left, right = start, index
        while left < right and chars[left].value == " ":
            left += 1
        while right > left and chars[right - 1].value == " ":
            right -= 1
        if left < right:
            blocks.append(
                StructureBlock(
                    block_id=f"b{len(blocks) + 1}",
                    text="".join(item.value for item in chars[left:right]),
                    location=_location(chars, left, right, parser.malformed),
                )
            )
        start = index + 1
    return normalized, blocks, parser.malformed


def extract_plain_blocks(source: str) -> tuple[str, list[StructureBlock]]:
    normalized_lines = [" ".join(line.split()) for line in source.splitlines()]
    normalized = "\n".join(line for line in normalized_lines if line)
    blocks: list[StructureBlock] = []
    cursor = 0
    for line in normalized.splitlines():
        blocks.append(
            StructureBlock(
                block_id=f"b{len(blocks) + 1}",
                text=line,
                location=SourceLocation(
                    cursor,
                    cursor + len(line),
                    None,
                    None,
                    [],
                    None,
                    "UNAVAILABLE",
                    reason="PLAIN_TEXT_HAS_NO_HTML_SOURCE_MAP",
                ),
            )
        )
        cursor += len(line) + 1
    return normalized, blocks


def validate_location(
    source: str, location: SourceLocation, expected_text: str | None = None
) -> bool:
    if location.coordinate_status == "UNAVAILABLE" or not location.source_html_spans:
        return False
    previous = -1
    for span in location.source_html_spans:
        if not (0 <= span.start < span.end <= len(source)) or span.start < previous:
            return False
        previous = span.end
    if expected_text is not None:
        raw = "".join(
            source[span.start : span.end] for span in location.source_html_spans
        )
        if " ".join(html.unescape(raw).split()) != " ".join(expected_text.split()):
            return False
    return True


__all__ = ["extract_html_blocks", "extract_plain_blocks", "validate_location"]

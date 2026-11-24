"""Body selection and source coordinates for the 0206 rules."""

import re
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass

from qa_eval.calculators.policy_structure_compliance.parsers import (
    parse_structure,
)
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

LINK_ONLY = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
LINK = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*["']?(?!["'#]|javascript:|mailto:)[^\s>]+[^>]*>.*?</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)
HTML_TAG = re.compile(r"<[^>]*>")
CELL_START = re.compile(r"<(td|th)\b[^>]*>", re.IGNORECASE)
HIDDEN = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
CELL = re.compile(r"(/tr\[\d+\])/td\[(\d+)\]")
TABLE = re.compile(r"/table\[\d+\]")
SENTENCE = re.compile(r"[^。；;！？!？\n]+")
SCOPE = re.compile(
    r"适用|(?:^|\s)对[于象]|针对|期间|阶段|类别|专项|(?:年|月|日)(?:前|后)|试点|附[表录件]|[一二三四五六七八九十]+类|[城乡]区"
)
JUNK = {"null", "none", "undefined", "nan", "nil", "n/a", "无", "暂无", "-", "—"}
LINK_LABEL = re.compile(
    r"(?:(?:请)?(?:点击|下载|查看|详见|附件|正文|原文|链接|如下|这里|全文|内容)|[：:。\s])*"
)


@dataclass(frozen=True)
class Passage:
    text: str
    location: dict
    scope: tuple[str, ...]
    table: tuple[str, int, int] | None
    header: bool = False


def _mask(match: re.Match) -> str:
    return "".join("\n" if char == "\n" else " " for char in match.group())


def passages(record: SampleRecordV2) -> tuple[list[Passage], dict, str | None]:
    bodies = {
        item.role: item
        for item in reversed(record.contents)
        if isinstance(item.payload, InlineTextPayload) and item.payload.text.strip()
    }
    item = bodies.get("article.body.html") or bodies.get("article.body.plain")
    if item is None:
        return [], {}, "EMPTY_BODY"
    source = item.payload.text
    is_html = item.role.endswith(".html")
    cleaned = HIDDEN.sub(_mask, source) if is_html else source
    if is_html:
        without_links, count = LINK.subn(
            lambda match: (
                ""
                if len(HTML_TAG.sub("", match.group()).strip()) <= 160
                else match.group()
            ),
            cleaned,
        )
        if count and LINK_LABEL.fullmatch(HTML_TAG.sub("", without_links)):
            return [], {"body_role": item.role}, "EXTERNAL_LINK_ONLY"
    mapped = (
        re.sub(r"(<\s*/?\s*)th(?=[\s>/])", r"\1td", cleaned, flags=re.IGNORECASE)
        if is_html
        else cleaned
    )
    parsed = parse_structure(
        html_source=mapped if is_html else None,
        plain_source=None if is_html else source,
        title="",
        metadata={},
    )
    visible = parsed.normalized_text.strip()
    meta = {
        "body_role": item.role,
        "parse_mode": parsed.mode,
        "visible_chars": len(visible),
        "blocks": len(parsed.blocks),
        "malformed_html": any(
            d["code"] == "MALFORMED_HTML_BLOCK_ONLY" for d in parsed.diagnostics
        ),
    }
    if not visible:
        if is_html and "article.body.plain" in bodies:
            plain_record = record.model_copy(
                update={"contents": [bodies["article.body.plain"]]}
            )
            return passages(plain_record)
        return [], meta, "EMPTY_BODY"
    placeholder = visible.casefold().strip("\"'“”‘’。. ")
    if placeholder in JUNK or (
        placeholder and all(word in JUNK for word in placeholder.split())
    ):
        return [], meta, "PLACEHOLDER_BODY"
    if all(LINK_ONLY.fullmatch(word) for word in visible.split()):
        return [], meta, "EXTERNAL_LINK_ONLY"

    by_offset = {node.location.normalized_text_start: node for node in parsed.nodes}
    by_id = {node.node_id: node for node in parsed.nodes}
    path = ()
    scopes: dict[int, str] = {}
    active_scope = ()
    previous_scope = ()
    plain_lines = (
        iter(re.finditer(r"[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+", source))
        if not is_html
        else None
    )
    result = []
    cell_tags = list(CELL_START.finditer(cleaned)) if is_html else []
    cell_starts = [match.start() for match in cell_tags]
    cell_headers: dict[str, bool] = {}
    row_numbers: dict[str, int] = {}
    table_rows: Counter[str] = Counter()
    span_tables = bool(
        is_html
        and re.search(r"<t[dh]\b[^>]*\b(?:rowspan|colspan)\s*=", source, re.IGNORECASE)
    )
    for block in parsed.blocks:
        loc = block.location
        node = by_offset.get(loc.normalized_text_start)
        prefix = 0
        if node:
            ancestry = []
            ancestor = node
            while ancestor:
                ancestry.append(ancestor.raw_number)
                ancestor = by_id.get(ancestor.parent_id)
            path = tuple(reversed(ancestry))
            prefix = len(node.raw_number.strip())
            for depth in tuple(scopes):
                if depth >= node.depth:
                    del scopes[depth]
            heading = block.text[prefix:].strip()
            if heading and (
                (
                    node.node_type in {"CHAPTER", "SECTION"}
                    and heading not in {"总则", "附则"}
                )
                or (SCOPE.search(heading) and heading.endswith(("：", ":")))
            ):
                scopes[node.depth] = heading
            active_scope = tuple(scopes.values())
            previous_scope = ()
        dom = loc.dom_path or ""
        cells = list(CELL.finditer(dom))
        table = None
        header = False
        if cells:
            cell = cells[-1]
            tables = list(TABLE.finditer(dom, 0, cell.start()))
            if tables:
                table_path = dom[: tables[-1].end()]
                row_path = dom[: cell.end(1)]
                if row_path not in row_numbers:
                    table_rows[table_path] += 1
                    row_numbers[row_path] = table_rows[table_path]
                table = (table_path, row_numbers[row_path], int(cell[2]))
                cell_path = dom[: cell.end()]
                if cell_path not in cell_headers and loc.source_html_start is not None:
                    # th was mapped to td at identical source offsets.
                    index = bisect_right(cell_starts, loc.source_html_start) - 1
                    cell_headers[cell_path] = (
                        index >= 0 and cell_tags[index][1].lower() == "th"
                    )
                header = cell_headers.get(cell_path, False)
        source_spans = [asdict(span) for span in loc.source_html_spans]
        if plain_lines is not None:
            line = next(line for line in plain_lines if line.group().strip())
            source_spans = [{"start": line.start(), "end": line.end()}]
        location = {
            "content_id": item.content_id,
            "block_id": block.block_id,
            "clause_path": list(path),
            "dom_path": loc.dom_path,
            "source_spans": source_spans,
            "coordinate_space": loc.coordinate_space
            if is_html
            else "source_plain_unicode_code_points",
            "coordinate_status": loc.coordinate_status if is_html else "BLOCK_ONLY",
            "normalized_block_start": loc.normalized_text_start,
            "table_cell": list(table) if table else None,
            "table_has_spans": span_tables if table else False,
        }
        if re.search(
            r"^(?:适用范围|适用对象|仅适用|下列|对于|针对|例外|特殊情况).{0,100}[：:]$",
            block.text[prefix:].strip(),
        ):
            previous_scope = (block.text[prefix:].strip(),)
        result.append(
            Passage(
                block.text[prefix:],
                location,
                active_scope + previous_scope,
                table,
                header,
            )
        )
        location["normalized_block_start"] += prefix
    return result, meta, None

"""Source-coordinate block parser and conservative document segmentation."""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass

TYPES = (
    "决议",
    "决定",
    "命令",
    "公报",
    "公告",
    "通告",
    "意见",
    "通知",
    "通报",
    "报告",
    "请示",
    "批复",
    "议案",
    "函",
    "纪要",
    "办法",
    "规定",
    "细则",
    "方案",
    "规划",
    "纲要",
    "条例",
    "规则",
    "指南",
    "工作要点",
)


INCOMPLETE_TAG = re.compile(r"<(?:/?[A-Za-z][\w:-]*|!--)[^<>]*$")


TRUNCATION = re.compile(
    r"(?:\[|【|[（(])?\s*(?:正文截断|内容截断|剩余内容略|点击查看全文|查看全文)\s*(?:\]|】|[）)])?[。.]?\s*$"
)

LEGAL = TYPES[:15]


POLICY = TYPES[15:]


SUFFIX = re.compile(
    r"(会议纪要|" + "|".join(sorted(TYPES, key=len, reverse=True)) + r"|令)$"
)


ORDER_TITLE = re.compile(r"^.{2,50}(?:命令|令)\s*[（(]?第?\s*[0-9]+\s*号[）)]?$")


QUOTED = re.compile(r"《([^《》]+)》|〈([^〈〉]+)〉")


FORWARD = re.compile(r"印发|转发|批转|印转")


ATTACH_LINE = re.compile(r"^附件\s*([0-9一二三四五六七八九十]*)\s*[：:]?\s*$")


ATTACH_NOTE = re.compile(r"^附件\s*[：:]\s*(.*)$")


LIST_ITEM = re.compile(r"^([1-9][0-9]*)[.．、]\s*(.+)$")


EXT_LINK = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*[\"']?[^\s<>\"']+\.(?:pdf|docx?|wps|zip|xlsx?)(?=[?#\s\"'>])",
    re.IGNORECASE,
)


def compact(value):
    return re.sub(r"\s+", "", value)


def type_name(title):
    # Strip quoted works before interpreting a title's outer suffix.
    outer = re.sub(r"《[^》]*》|〈[^〉]*〉", "", title).strip()
    outer = re.sub(r"[（(](?:试行|暂行|修订|修订稿|征求意见稿)[）)]\s*$", "", outer)
    outer = outer.rstrip("。！？ )）")
    if ORDER_TITLE.fullmatch(outer):
        return "命令"
    match = SUFFIX.search(outer)
    if not match:
        return None
    return {"会议纪要": "纪要", "令": "命令"}.get(match[1], match[1])


def policy_quotes(title):
    return [
        name
        for match in QUOTED.finditer(title)
        if (name := match[1] or match[2]) and type_name(name) in POLICY
    ]


def external_asset(row, body):
    return state(row.get("attachurl")) == "present" or bool(EXT_LINK.search(body))


MARKUP = re.compile(
    r"<!--[\s\S]*?(?:-->|\Z)|<(?=[/!?]?[A-Za-z])(?:[^<>'\"]|\"[^\"]*\"|'[^']*')+>|\r\n|\n|\r"
)


TAG_NAME = re.compile(r"<\s*/?\s*([a-z][\w:-]*)", re.IGNORECASE)


BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "table",
    "hr",
    "ul",
    "ol",
    "pre",
    "blockquote",
    "center",
}


NON_BODY = re.compile(
    r"<(script|style|head)\b[^>]*>.*?(?:</\1\s*>|\Z)", re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class Block:
    text: str
    start: int
    end: int
    tag: str = "p"
    content_id: str = "content"

    @property
    def heading_level(self):
        return int(self.tag[1]) if re.fullmatch(r"h[1-6]", self.tag) else None

    @property
    def kind(self):
        if self.heading_level:
            return "HEADING"
        return {
            "li": "LIST_ITEM",
            "td": "TABLE_CELL",
            "th": "TABLE_HEADER",
            "tr": "TABLE_ROW",
        }.get(self.tag, "PARAGRAPH")

    def span(self):
        return {**span(self.start, self.end), "content_id": self.content_id}


def span(start, end, field="content"):
    return {
        "field": field,
        "coordinate_space": "source_unicode_codepoint",
        "start": start,
        "end": end,
    }


def blocks(source, content_id="content", is_html=True):
    if not is_html:
        return [
            Block(
                m[0].strip(),
                m.start() + len(m[0]) - len(m[0].lstrip()),
                m.start() + len(m[0].rstrip()),
                content_id=content_id,
            )
            for m in re.finditer(r"[^\r\n]+", source)
            if m[0].strip()
        ]
    # Spaces mask non-body regions without changing raw coordinates.
    cleaned = NON_BODY.sub(lambda m: " " * len(m[0]), source)
    text_parts = []
    out = []
    start = None
    end = None
    dom = []

    def flush():
        nonlocal start, end
        text = html.unescape("".join(text_parts)).strip()
        if text:
            out.append(Block(text, start, end, dom[-1] if dom else "p", content_id))
        text_parts.clear()
        start = end = None

    def add(lo, hi):
        nonlocal start, end
        raw = cleaned[lo:hi]
        if raw.strip():
            if start is None:
                start = lo + len(raw) - len(raw.lstrip())
            end = lo + len(raw.rstrip())
        text_parts.append(raw)

    left = 0
    for token in MARKUP.finditer(cleaned):
        add(left, token.start())
        tag = TAG_NAME.match(token[0])
        if token[0].startswith(("\n", "\r")) or (tag and tag[1].lower() in BLOCK_TAGS):
            flush()
            if tag:
                name = tag[1].lower()
                if token[0].startswith("</"):
                    if name in dom:
                        del dom[len(dom) - 1 - dom[::-1].index(name) :]
                elif name not in {"br", "hr"} and not token[0].endswith("/>"):
                    dom.append(name)
        left = token.end()
    add(left, len(cleaned))
    flush()
    return out


def clean_title(text):
    return compact(text).strip("《》〈〉")


def title_span(row):
    return span(0, len(row["title"]), "title") if row.get("title") else None


def find_boundaries(row, body, lines, title, title_type):
    names = {clean_title(name): name for name in policy_quotes(title)}
    declared = []
    for ix, block in enumerate(lines):
        note = ATTACH_NOTE.fullmatch(block.text)
        if not note:
            continue
        first = note[1].strip()
        if not first and (
            ix + 1 == len(lines) or not LIST_ITEM.fullmatch(lines[ix + 1].text)
        ):
            continue
        if first:
            item = LIST_ITEM.fullmatch(first)
            declared.append(
                (item[1] if item else None, item[2] if item else first, block)
            )
        for item_block in lines[ix + 1 : ix + 16]:
            item = LIST_ITEM.fullmatch(item_block.text)
            if not item:
                break
            declared.append((item[1], item[2], item_block))

    known = set(names) | {clean_title(name) for _, name, _ in declared}
    notice_open = title_type == "通知" and any(
        FORWARD.search(QUOTED.sub("", block.text)) for block in lines[:10]
    )
    required = (
        title_type == "通知"
        and bool(names)
        and (bool(FORWARD.search(QUOTED.sub("", title))) or notice_open)
    )
    found = []
    ambiguous = []
    # A matching heading needs an independent paragraph and a following body block.
    for ix, block in enumerate(lines[1:], 1):
        normalized = clean_title(block.text)
        if normalized not in known or ix + 1 >= len(lines):
            continue
        if not (
            ATTACH_LINE.fullmatch(lines[ix - 1].text)
            or any(
                name == normalized and note.start < block.start
                for _, raw_name, note in declared
                if (name := clean_title(raw_name))
            )
            or (
                required
                and any(
                    FORWARD.search(QUOTED.sub("", prior.text)) for prior in lines[:ix]
                )
            )
        ):
            ambiguous.append(block.span())
            continue
        following = lines[ix + 1]
        if (
            len(compact(following.text)) < 12
            or ATTACH_NOTE.fullmatch(following.text)
            or ATTACH_LINE.fullmatch(following.text)
            or clean_title(following.text) in known
        ):
            ambiguous.append(block.span())
            continue
        header = ATTACH_LINE.fullmatch(lines[ix - 1].text)
        start = ix - 1 if header else ix
        if any(note.start == block.start for _, _, note in declared):
            continue
        found.append(
            {
                "line_ix": start,
                "title_ix": ix,
                "name": block.text,
                "number": (header[1] or None) if header else None,
                "title_location": block.span(),
                "evidence": [block.span(), following.span()],
            }
        )
        if header:
            found[-1]["evidence"].insert(0, lines[ix - 1].span())
    # Explicit names without guillemets may still declare a substantive policy attachment.
    if notice_open and (
        any(type_name(name) in POLICY for _, name, _ in declared)
        or any(type_name(item["name"]) in POLICY for item in found)
    ):
        required = True
    duplicates = Counter(clean_title(item["name"]) for item in found)
    if any(count > 1 for count in duplicates.values()):
        ambiguous.extend(
            item["title_location"]
            for item in found
            if duplicates[clean_title(item["name"])] > 1
        )
        found = [item for item in found if duplicates[clean_title(item["name"])] == 1]
    # Split only after confirming that candidate blocks can actually be separated.
    unresolved = bool(ambiguous) or (
        required
        and (
            not found
            or not set(names).issubset({clean_title(x["name"]) for x in found})
        )
    )
    status = (
        "BOUNDARY_CANDIDATE"
        if found and not unresolved
        else "AMBIGUOUS"
        if ambiguous or (found and unresolved)
        else "EXTERNAL_ONLY_CANDIDATE"
        if required and external_asset(row, body)
        else "REQUIRED_BOUNDARY_MISSING"
        if required
        else "NO_REQUIRED_ATTACHMENT_SIGNAL"
    )
    scope = {
        "required": required,
        "unresolved": unresolved,
        "status": status,
        "references": [{"number": n, "location": b.span()} for n, _, b in declared],
        "quoted_policy_types": [type_name(name) for name in names.values()],
        "external_asset_signal": external_asset(row, body),
        "inline_marker_signal": any(ATTACH.search(block.text) for block in lines),
        "ambiguous_locations": ambiguous,
    }
    return scope, found, declared


ATTACH = re.compile(
    r"(?:^|\n)[ \t\u3000]*(?:附[件录][ \t]*[：:]|附件[ \t]*(?:\d+|[一二三四五六七八九十]+)?[ \t]*(?:\n|$))"
)


def state(value):
    if value is None:
        return "null"
    if isinstance(value, str) and value.strip() in {"", "None"}:
        return "empty"
    if isinstance(value, (list, dict)) and not value:
        return "empty"
    return "present"

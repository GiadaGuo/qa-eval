from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar

from qa_eval.calculators.policy_structure_compliance.html_source_map import (
    extract_html_blocks,
    extract_plain_blocks,
)
from qa_eval.calculators.policy_structure_compliance.schema import (
    StructureBlock,
    StructureNode,
    StructureParseResult,
)

PARSER_VERSION = "1.0.0"
LEGAL_RE = re.compile(r"^\s*第([〇零一二三四五六七八九十百千万两\d]+)(章|节|条|款)\s*")
CN_OUTLINE_RE = re.compile(r"^\s*([一二三四五六七八九十百]+)、\s*")
CN_PAREN_RE = re.compile(r"^\s*[（(]([一二三四五六七八九十百]+)[）)]\s*")
ARABIC_RE = re.compile(r"^\s*(\d{1,3})[.、](?!\d)\s*")
ARABIC_PAREN_RE = re.compile(r"^\s*[（(](\d{1,3})[）)]\s*")
REFERENCE_PREFIX_RE = re.compile(r"^(?:根据|依据|按照|依照|违反|符合|参照).{0,24}$")
LEGAL_NODE_TYPES = {"CHAPTER", "SECTION", "ARTICLE", "PARAGRAPH"}


CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def number_value(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    total = section = digit = 0
    for char in raw:
        if char in CN_DIGITS:
            digit = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if unit == 10000:
                total += (section + digit) * unit
                section = digit = 0
            else:
                section += (digit or 1) * unit
                digit = 0
        else:
            return None
    return total + section + digit


def classify_document_type(
    metadata: dict[str, Any], title: str
) -> tuple[str, str, float]:
    extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
    trusted = metadata.get("document_type") or extra.get("policyType")
    if isinstance(trusted, str) and trusted.strip():
        return trusted.strip(), "METADATA", 0.9
    for value in ("通知", "办法", "条例", "规划", "纲要", "批复"):
        if value in title:
            return value, "TITLE_INFERENCE", 0.65
    return "UNKNOWN", "UNAVAILABLE", 0.0


def _candidate(block: StructureBlock) -> tuple[str, int, str, int] | None:
    match = LEGAL_RE.match(block.text)
    if match:
        raw, suffix = match.groups()
        return (
            {"章": "CHAPTER", "节": "SECTION", "条": "ARTICLE", "款": "PARAGRAPH"}[
                suffix
            ],
            number_value(raw) or 0,
            match.group(0).strip(),
            match.end(),
        )
    for pattern, node_type, depth in (
        (CN_OUTLINE_RE, "OUTLINE_L1", 1),
        (CN_PAREN_RE, "OUTLINE_L2", 2),
        (ARABIC_RE, "OUTLINE_L3", 3),
        (ARABIC_PAREN_RE, "OUTLINE_L4", 4),
    ):
        match = pattern.match(block.text)
        if match:
            return (
                node_type,
                number_value(match.group(1)) or 0,
                match.group(0).strip(),
                match.end(),
            )
    return None


class LegalClauseParser:
    """Classifies block-anchored chapter/section/article/paragraph candidates."""

    depths: ClassVar[dict[str, int]] = {
        "CHAPTER": 1,
        "SECTION": 2,
        "ARTICLE": 3,
        "PARAGRAPH": 4,
    }

    @classmethod
    def accepts(cls, node_type: str) -> bool:
        return node_type in cls.depths


class OutlineParser:
    """Classifies four levels of administrative-document outline numbering."""

    @staticmethod
    def accepts(node_type: str) -> bool:
        return node_type.startswith("OUTLINE_L")

    @staticmethod
    def depth(node_type: str) -> int:
        return int(node_type[-1])


class GenericDocumentParser:
    """Represents valid block documents without explicit numbering nodes."""

    @staticmethod
    def applicable(candidate_count: int) -> bool:
        return candidate_count == 0


def parse_structure(
    *,
    html_source: str | None,
    plain_source: str | None,
    title: str,
    metadata: dict[str, Any],
) -> StructureParseResult:
    if html_source is not None:
        normalized, blocks, malformed = extract_html_blocks(html_source)
    else:
        normalized, blocks = extract_plain_blocks(plain_source or "")
        malformed = False
    document_type, type_source, confidence = classify_document_type(metadata, title)
    candidates = [(block, value) for block in blocks if (value := _candidate(block))]
    legal = sum(LegalClauseParser.accepts(value[0]) for _, value in candidates)
    outline = len(candidates) - legal
    mode = (
        "MIXED"
        if legal and outline
        else "LEGAL_HIERARCHY"
        if legal
        else "OUTLINE_HIERARCHY"
        if outline
        else "GENERIC_DOCUMENT"
    )
    mode_candidates = [
        {
            "mode": "LEGAL_HIERARCHY",
            "score": min(1.0, legal / 2),
            "reasons": [f"legal_candidates:{legal}"],
        },
        {
            "mode": "OUTLINE_HIERARCHY",
            "score": min(1.0, outline / 2),
            "reasons": [f"outline_candidates:{outline}"],
        },
        {
            "mode": "GENERIC_DOCUMENT",
            "score": 1.0 if not candidates else 0.1,
            "reasons": ["fallback"],
        },
    ]
    nodes: list[StructureNode] = []
    parents: dict[int, StructureNode] = {}
    for block, (node_type, ordinal, raw_number, _end) in candidates:
        depth = (
            LegalClauseParser.depths[node_type]
            if LegalClauseParser.accepts(node_type)
            else OutlineParser.depth(node_type)
        )
        parent = next(
            (parents[level] for level in range(depth - 1, 0, -1) if level in parents),
            None,
        )
        node = StructureNode(
            node_id=f"n{len(nodes) + 1}",
            node_type=node_type,
            parent_id=parent.node_id if parent else None,
            depth=depth,
            ordinal=ordinal,
            raw_number=raw_number,
            numbering_scope=f"{parent.node_id if parent else 'root'}:{node_type}",
            location=block.location,
        )
        nodes.append(node)
        parents[depth] = node
        for level in list(parents):
            if level > depth:
                del parents[level]
    diagnostics = []
    if malformed:
        diagnostics.append({"code": "MALFORMED_HTML_BLOCK_ONLY", "severity": "WARNING"})
    if mode == "MIXED":
        diagnostics.append({"code": "MIXED_MODE_SELECTED", "severity": "INFO"})
    if document_type == "通知":
        diagnostics.append(
            {
                "code": "NOTIFICATION_RECOGNIZED",
                "severity": "INFO",
                "source": type_source,
            }
        )
    policy = hashlib.sha256(f"{PARSER_VERSION}:block-v1:mode-v1".encode()).hexdigest()
    return StructureParseResult(
        parser_key="policy-structure-parser",
        parser_version=PARSER_VERSION,
        document_type=document_type,
        document_type_source=type_source,
        document_type_confidence=confidence,
        mode=mode,  # type: ignore[arg-type]
        mode_candidates=mode_candidates,
        normalized_text=normalized,
        blocks=blocks,
        nodes=nodes,
        diagnostics=[
            {"code": "PARSER_POLICY", "fingerprint": f"sha256:{policy}"},
            *diagnostics,
        ],
    )


__all__ = [
    "PARSER_VERSION",
    "GenericDocumentParser",
    "LegalClauseParser",
    "OutlineParser",
    "classify_document_type",
    "number_value",
    "parse_structure",
]

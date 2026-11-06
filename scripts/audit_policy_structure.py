"""Stream and reproducibly profile policy-document structure.

This is a Phase A-C audit utility, not a production Calculator.  It keeps only
bounded stratified reservoirs in memory and writes identifiers and aggregate
features, never document bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

STRATA = (
    "notification",
    "measure",
    "regulation",
    "plan",
    "other",
    "no_numbering",
    "html_anomaly",
    "attachment_only",
    "notification_with_legal_clauses",
)
TAG_PATTERN = re.compile(r"<\s*([a-zA-Z][\w:-]*)\b")
LEGAL_PATTERN = re.compile(
    r"(?m)^\s*第[一二三四五六七八九十百千万〇零两\d]+(?:章|节|条|款)"
)
OUTLINE_PATTERN = re.compile(
    r"(?m)^\s*(?:[一二三四五六七八九十百]+、|[（(][一二三四五六七八九十百\d]+[）)]|\d{1,3}[.、])"
)
NUMBER_PATTERNS = {
    "chapter_cn": re.compile(r"(?m)^\s*第[一二三四五六七八九十百千万〇零两]+章"),
    "chapter_arabic": re.compile(r"(?m)^\s*第\d+章"),
    "section": re.compile(r"(?m)^\s*第[一二三四五六七八九十百千万〇零两\d]+节"),
    "article": re.compile(r"(?m)^\s*第[一二三四五六七八九十百千万〇零两\d]+条"),
    "paragraph": re.compile(r"(?m)^\s*第[一二三四五六七八九十百千万〇零两\d]+款"),
    "cn_outline": re.compile(r"(?m)^\s*[一二三四五六七八九十百]+、"),
    "cn_parenthesized": re.compile(r"(?m)^\s*[（(][一二三四五六七八九十百]+[）)]"),
    "arabic_outline": re.compile(r"(?m)^\s*\d{1,3}[.、](?!\d)"),
    "arabic_parenthesized": re.compile(r"(?m)^\s*[（(]\d{1,3}[）)]"),
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class StructureHTMLParser(HTMLParser):
    """Collect tag counts and conservative nesting diagnostics."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: Counter[str] = Counter()
        self.stack: list[str] = []
        self.unmatched_end = 0
        self.max_depth = 0
        self.entity_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.tags[tag] += 1
        if tag not in VOID_TAGS:
            self.stack.append(tag)
            self.max_depth = max(self.max_depth, len(self.stack))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag.lower()] += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self.stack:
            self.unmatched_end += 1
            return
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_entityref(self, name: str) -> None:
        self.entity_count += 1

    def handle_charref(self, name: str) -> None:
        self.entity_count += 1


@dataclass
class Reservoir:
    size: int
    rng: random.Random
    seen: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, item: dict[str, Any]) -> None:
        self.seen += 1
        if len(self.items) < self.size:
            self.items.append(item)
            return
        replacement = self.rng.randrange(self.seen)
        if replacement < self.size:
            self.items[replacement] = item


def document_type(record: dict[str, Any]) -> str:
    combined = " ".join(
        str(record.get(key) or "") for key in ("policyType", "type", "title")
    )
    if "通知" in combined:
        return "notification"
    if "办法" in combined:
        return "measure"
    if "条例" in combined:
        return "regulation"
    if "规划" in combined or "纲要" in combined:
        return "plan"
    return "other"


def content_of(record: dict[str, Any]) -> str:
    return str(record.get("content") or record.get("plain_text_content") or "")


def structural_text(content: str) -> str:
    """Create audit-only line boundaries; never use its offsets as source offsets."""
    return re.sub(r"<[^>]*>", "\n", content)


def sample_id_of(record: dict[str, Any], line_number: int) -> str:
    for key in ("iid", "id", "article_id", "sample_id"):
        if record.get(key) not in (None, ""):
            return str(record[key])
    return f"line:{line_number}"


def attachment_only(record: dict[str, Any], content: str) -> bool:
    attachment = any(
        record.get(key) for key in ("attachurl", "attachment_url", "linkhref")
    )
    image_or_link = bool(
        re.search(
            r"<(?:img|iframe|object|embed)\b|\.pdf(?:[?#\"']|$)",
            content,
            re.IGNORECASE,
        )
    )
    visible = re.sub(r"<[^>]*>", "", content)
    visible = re.sub(r"&\w+;|&#\d+;", "", visible)
    return (attachment or image_or_link) and len("".join(visible.split())) < 50


def has_html_anomaly(content: str) -> bool:
    if not content:
        return False
    if content.count("<") != content.count(">"):
        return True
    parser = StructureHTMLParser()
    try:
        parser.feed(content)
        parser.close()
    except (AssertionError, ValueError):
        return True
    return parser.unmatched_end > 0 or bool(parser.stack)


def strata_for(record: dict[str, Any], content: str) -> set[str]:
    kind = document_type(record)
    strata = {kind}
    candidate_text = structural_text(content)
    has_legal = bool(LEGAL_PATTERN.search(candidate_text))
    has_outline = bool(OUTLINE_PATTERN.search(candidate_text))
    if not has_legal and not has_outline:
        strata.add("no_numbering")
    if has_html_anomaly(content):
        strata.add("html_anomaly")
    if attachment_only(record, content):
        strata.add("attachment_only")
    if kind == "notification" and has_legal:
        strata.add("notification_with_legal_clauses")
    return strata


def safe_sample(
    record: dict[str, Any], line_number: int, categories: Iterable[str]
) -> dict[str, Any]:
    content = content_of(record)
    return {
        "sample_id": sample_id_of(record, line_number),
        "line_number": line_number,
        "document_type": document_type(record),
        "strata": sorted(categories),
        "content_length_chars": len(content),
    }


def profile_samples(
    samples: list[dict[str, Any]], records_by_id: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any]:
    tag_counts: Counter[str] = Counter()
    pattern_documents: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    html_documents = entities = nested = anomalies = 0
    for sample in samples:
        record = records_by_id[(sample["sample_id"], sample["line_number"])]
        content = content_of(record)
        candidate_text = structural_text(content)
        parser = StructureHTMLParser()
        if TAG_PATTERN.search(content):
            html_documents += 1
            try:
                parser.feed(content)
                parser.close()
            except (AssertionError, ValueError):
                anomalies += 1
            tag_counts.update(parser.tags)
            entities += parser.entity_count
            nested += int(parser.max_depth >= 5)
            anomalies += int(parser.unmatched_end > 0 or bool(parser.stack))
        matched = {
            name
            for name, pattern in NUMBER_PATTERNS.items()
            if pattern.search(candidate_text)
        }
        pattern_documents.update(matched)
        legal = bool(LEGAL_PATTERN.search(candidate_text))
        outline = bool(OUTLINE_PATTERN.search(candidate_text))
        mode = (
            "MIXED"
            if legal and outline
            else "LEGAL_HIERARCHY"
            if legal
            else "OUTLINE_HIERARCHY"
            if outline
            else "GENERIC_DOCUMENT"
        )
        mode_counts[mode] += 1
    return {
        "sampled_unique_record_count": len(samples),
        "html": {
            "document_count": html_documents,
            "tag_counts": dict(tag_counts.most_common()),
            "entity_reference_count": entities,
            "deep_nesting_document_count": nested,
            "nesting_anomaly_document_count": anomalies,
        },
        "numbering_pattern_document_counts": dict(pattern_documents),
        "structure_mode_candidate_counts": dict(mode_counts),
    }


def audit(input_path: Path, sample_size: int, seed: int) -> dict[str, Any]:
    reservoirs = {
        name: Reservoir(sample_size, random.Random(f"{seed}:{name}")) for name in STRATA
    }
    type_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    invalid_json = 0
    input_sha256 = hashlib.sha256()
    retained: dict[tuple[str, int], dict[str, Any]] = {}
    total = 0
    with input_path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            input_sha256.update(raw)
            if not raw.strip():
                continue
            total += 1
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid_json += 1
                continue
            content = content_of(record)
            categories = strata_for(record, content)
            type_counts[document_type(record)] += 1
            category_counts.update(categories)
            sample = safe_sample(record, line_number, categories)
            for category in categories:
                reservoirs[category].add(sample)

    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for reservoir in reservoirs.values():
        for item in reservoir.items:
            selected[(item["sample_id"], item["line_number"])] = item

    wanted_lines = {line for _, line in selected}
    with input_path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if line_number in wanted_lines:
                record = json.loads(raw)
                retained[(sample_id_of(record, line_number), line_number)] = record

    stratified = {
        name: {
            "population_count": reservoirs[name].seen,
            "samples": sorted(
                reservoirs[name].items, key=lambda item: item["line_number"]
            ),
        }
        for name in STRATA
    }
    return {
        "schema_version": "1.0",
        "audit_kind": "POLICY_STRUCTURE_PHASE_B",
        "input": {
            "path": str(input_path),
            "sha256": input_sha256.hexdigest(),
            "record_count": total,
            "invalid_json_count": invalid_json,
        },
        "sampling": {
            "algorithm": "per-stratum-reservoir",
            "seed": seed,
            "sample_size_per_stratum": sample_size,
        },
        "full_scan_counts": {
            "document_type": dict(type_counts),
            "strata": dict(category_counts),
        },
        "stratified_samples": stratified,
        "sample_profile": profile_samples(list(selected.values()), retained),
        "limitations": [
            "Document type is inferred from policyType/type/title and is not a verified legal classification.",
            "Numbering regex hits are candidates, not parser accuracy or segmentation ground truth.",
            "HTML nesting diagnostics use tolerant lexical parsing and are not browser DOM conformance results.",
            "No source HTML to normalized text offset mapping is inferred by this audit.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261106)
    args = parser.parse_args()
    if args.sample_size < 1:
        parser.error("--sample-size must be positive")
    result = audit(args.input, args.sample_size, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

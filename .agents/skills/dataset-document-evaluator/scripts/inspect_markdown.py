#!/usr/bin/env python3
"""Inspect a Markdown dataset document without making semantic judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not valid UTF-8: {path}: {exc}") from exc


def evidence_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "manifest-or-profile"
    if suffix in {".jsonl", ".ndjson"}:
        return "dataset-snapshot"
    if suffix in {".md", ".txt"}:
        return "text-evidence"
    return "other"


def inspect(document: Path, evidence: list[Path]) -> dict[str, Any]:
    if not document.is_file():
        raise ValueError(f"document does not exist or is not a file: {document}")
    if document.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError(f"MVP supports Markdown only: {document}")

    text = read_utf8(document)
    if not text.strip():
        raise ValueError(f"document is empty: {document}")
    lines = text.splitlines()
    headings = []
    numbered_lines = []
    for number, line in enumerate(lines, start=1):
        numbered_lines.append({"number": number, "text": line})
        stripped = line.lstrip()
        if stripped.startswith("#"):
            marker = stripped.split(maxsplit=1)[0]
            if marker and set(marker) == {"#"} and len(marker) <= 6:
                title = stripped[len(marker) :].strip()
                if title:
                    headings.append({"line": number, "level": len(marker), "title": title})

    evidence_files = []
    for path in evidence:
        if not path.is_file():
            raise ValueError(f"evidence does not exist or is not a file: {path}")
        evidence_files.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "kind": evidence_kind(path),
            }
        )

    return {
        "document": {
            "path": str(document),
            "sha256": sha256_file(document),
            "size_bytes": document.stat().st_size,
            "line_count": len(lines),
        },
        "headings": headings,
        "numbered_lines": numbered_lines,
        "evidence_files": evidence_files,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, help="UTF-8 Markdown document")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        type=Path,
        help="Optional evidence path; repeat for multiple files",
    )
    parser.add_argument("--output", type=Path, help="Write JSON to this path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = inspect(args.document, args.evidence)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

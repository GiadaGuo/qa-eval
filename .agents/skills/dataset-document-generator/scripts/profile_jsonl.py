#!/usr/bin/env python3
"""Stream a JSONL snapshot into a deterministic dataset profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LENGTH_BINS = (
    ("0", 0, 0),
    ("1-99", 1, 99),
    ("100-499", 100, 499),
    ("500-999", 500, 999),
    ("1000-1999", 1000, 1999),
    ("2000-4999", 2000, 4999),
    ("5000+", 5000, None),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def percentile(sorted_values: list[int], fraction: float) -> int | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, math.floor((len(sorted_values) - 1) * fraction))
    return sorted_values[index]


def truncate(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "…"
    if isinstance(value, dict):
        return {str(key): truncate(child, max_chars) for key, child in value.items()}
    if isinstance(value, list):
        return [truncate(child, max_chars) for child in value[:20]]
    return value


def load_manifest(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    if not path.is_file():
        raise ValueError(f"manifest not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value, sha256_file(path)


def make_profile(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    snapshot: Path = args.snapshot
    if not snapshot.is_file():
        raise ValueError(f"snapshot not found: {snapshot}")
    snapshot_hash = sha256_file(snapshot)
    manifest, manifest_hash = load_manifest(args.manifest)

    errors: list[dict[str, Any]] = []
    failures: list[str] = []
    total_lines = valid_json = invalid_json = object_records = non_object_records = 0
    field_occurrences: Counter[str] = Counter()
    field_types: dict[str, Counter[str]] = {}
    id_missing = id_null = id_duplicate_extra = 0
    ids: set[str] = set()
    text_missing = text_null = text_empty = text_blank = text_non_string = text_chars = 0
    lengths: list[int] = []
    text_hash_counts: Counter[str] = Counter()
    bins: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    with snapshot.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            total_lines += 1
            try:
                record = json.loads(line)
                valid_json += 1
            except json.JSONDecodeError as exc:
                invalid_json += 1
                if len(errors) < args.error_limit:
                    errors.append({"line": line_number, "error": f"JSONDecodeError: {exc.msg}"})
                continue
            if not isinstance(record, dict):
                non_object_records += 1
                if len(errors) < args.error_limit:
                    errors.append({"line": line_number, "error": "record is not a JSON object"})
                continue
            object_records += 1
            for field, value in record.items():
                field = str(field)
                field_occurrences[field] += 1
                field_types.setdefault(field, Counter())[json_type(value)] += 1

            if args.id_field not in record:
                id_missing += 1
            elif record[args.id_field] is None:
                id_null += 1
            else:
                stable_id = json.dumps(record[args.id_field], ensure_ascii=False, sort_keys=True)
                if stable_id in ids:
                    id_duplicate_extra += 1
                ids.add(stable_id)

            if args.text_field not in record:
                text_missing += 1
            elif record[args.text_field] is None:
                text_null += 1
            elif not isinstance(record[args.text_field], str):
                text_non_string += 1
                if len(errors) < args.error_limit:
                    errors.append({"line": line_number, "error": f"{args.text_field} is not a string or null"})
            else:
                text = record[args.text_field]
                length = len(text)
                lengths.append(length)
                text_chars += length
                text_empty += length == 0
                text_blank += length > 0 and not text.strip()
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                text_hash_counts[digest] += 1
                for label, lower, upper in LENGTH_BINS:
                    if length >= lower and (upper is None or length <= upper):
                        bins[label] += 1
                        break

            if len(samples) < args.sample_limit:
                samples.append({"line": line_number, "record": truncate(record, args.sample_text_max_chars)})

    if invalid_json:
        failures.append(f"{invalid_json} invalid JSON line(s)")
    if non_object_records:
        failures.append(f"{non_object_records} non-object JSON record(s)")
    if field_occurrences.get(args.id_field, 0) == 0:
        failures.append(f"configured id field never occurs: {args.id_field}")
    if field_occurrences.get(args.text_field, 0) == 0:
        failures.append(f"configured text field never occurs: {args.text_field}")
    if text_non_string:
        failures.append(f"{text_non_string} non-string, non-null text value(s)")

    if manifest is not None:
        manifest_file = manifest.get("file")
        if manifest_file and Path(str(manifest_file)).name != snapshot.name:
            failures.append(f"manifest file does not match snapshot: {manifest_file}")
        manifest_sha = str(manifest.get("sha256", "")).removeprefix("sha256:")
        if manifest_sha and manifest_sha != snapshot_hash:
            failures.append("manifest SHA-256 does not match snapshot")
        if manifest.get("row_count") is not None and manifest.get("row_count") != total_lines:
            failures.append("manifest row_count does not match snapshot line count")

    sorted_lengths = sorted(lengths)
    duplicate_groups = sum(count > 1 for count in text_hash_counts.values())
    duplicate_extra = sum(count - 1 for count in text_hash_counts.values() if count > 1)
    profile = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "path": str(snapshot),
            "sha256": snapshot_hash,
            "size_bytes": snapshot.stat().st_size,
            "format": "jsonl",
        },
        "manifest": None if args.manifest is None else {"path": str(args.manifest), "sha256": manifest_hash},
        "parameters": {
            "id_field": args.id_field,
            "text_field": args.text_field,
            "sample_limit": args.sample_limit,
            "sample_text_max_chars": args.sample_text_max_chars,
            "length_bins": [label for label, _, _ in LENGTH_BINS],
            "exact_duplicate_algorithm": "sha256(UTF-8 text)",
        },
        "records": {
            "total_lines": total_lines,
            "valid_json": valid_json,
            "invalid_json": invalid_json,
            "object_records": object_records,
            "non_object_records": non_object_records,
        },
        "fields": {
            field: {"occurrences": count, "types": dict(sorted(field_types[field].items()))}
            for field, count in sorted(field_occurrences.items())
        },
        "id": {
            "field": args.id_field,
            "missing": id_missing,
            "null": id_null,
            "unique": len(ids),
            "duplicate_extra": id_duplicate_extra,
        },
        "text": {
            "field": args.text_field,
            "missing": text_missing,
            "null": text_null,
            "empty": text_empty,
            "blank_nonempty": text_blank,
            "non_string": text_non_string,
            "non_null_string": len(lengths),
            "total_characters": text_chars,
            "length": {
                "minimum": min(lengths) if lengths else None,
                "maximum": max(lengths) if lengths else None,
                "mean": round(statistics.mean(lengths), 2) if lengths else None,
                "median": statistics.median(lengths) if lengths else None,
                "p25": percentile(sorted_lengths, 0.25),
                "p75": percentile(sorted_lengths, 0.75),
                "p95": percentile(sorted_lengths, 0.95),
                "bins": {label: bins[label] for label, _, _ in LENGTH_BINS},
            },
        },
        "exact_text_duplicates": {"groups": duplicate_groups, "duplicate_extra": duplicate_extra},
        "samples": samples,
        "errors": errors,
        "status": "ERROR" if failures else "COMPLETED",
        "failure_reasons": failures,
    }
    return profile, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--id-field", required=True)
    parser.add_argument("--text-field", required=True)
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--sample-text-max-chars", type=int, default=240)
    parser.add_argument("--error-limit", type=int, default=20)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_limit < 0 or args.sample_text_max_chars < 1 or args.error_limit < 1:
        print("error: sample/error limits are invalid", file=sys.stderr)
        return 2
    try:
        profile, failures = make_profile(args)
        if args.output.exists():
            raise ValueError(f"refusing to overwrite output: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"profiled {profile['records']['total_lines']} records -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

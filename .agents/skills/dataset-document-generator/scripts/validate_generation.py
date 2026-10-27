#!/usr/bin/env python3
"""Validate dataset-document generation provenance and output isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FACT_STATES = {"EVIDENCE_VERIFIED", "DERIVED", "USER_PROVIDED", "UNRESOLVED"}
RUN_STATES = {"GENERATED", "GENERATED_WITH_UNRESOLVED_ITEMS", "BLOCKED_BY_MISSING_INPUT", "ERROR"}
MODES = {"draft", "strict"}
DEFAULT_BLOCKERS = {"0101-05", "0104-01"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def add_if_false(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def resolve_recorded_path(raw: str, base: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def validate(manifest: dict[str, Any], unresolved: dict[str, Any], manifest_path: Path) -> list[str]:
    errors: list[str] = []
    add_if_false(manifest.get("schema_version") == "1.0", "manifest schema_version must be 1.0", errors)
    add_if_false(unresolved.get("schema_version") == "1.0", "unresolved schema_version must be 1.0", errors)

    run = manifest.get("run")
    add_if_false(isinstance(run, dict), "run must be an object", errors)
    mode = run.get("mode") if isinstance(run, dict) else None
    run_id = run.get("run_id") if isinstance(run, dict) else None
    add_if_false(mode in MODES, "run.mode is invalid", errors)
    add_if_false(bool(run_id), "run.run_id is required", errors)
    add_if_false(bool(run.get("generated_at")) if isinstance(run, dict) else False, "run.generated_at is required", errors)
    add_if_false(unresolved.get("run_id") == run_id, "unresolved run_id does not match", errors)

    dataset = manifest.get("dataset")
    add_if_false(isinstance(dataset, dict), "dataset must be an object", errors)
    if isinstance(dataset, dict):
        add_if_false(bool(dataset.get("dataset_id")), "dataset_id is required", errors)
        add_if_false(bool(dataset.get("dataset_version")), "dataset_version is required", errors)

    registered_sources: set[tuple[str, str]] = set()
    input_realpaths: set[Path] = set()
    inputs = manifest.get("inputs")
    add_if_false(isinstance(inputs, list) and bool(inputs), "inputs must be a non-empty array", errors)
    if isinstance(inputs, list):
        for index, item in enumerate(inputs):
            prefix = f"inputs[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            raw_path, digest = item.get("path"), item.get("sha256")
            add_if_false(bool(raw_path), f"{prefix}.path is required", errors)
            add_if_false(bool(SHA_RE.match(str(digest or ""))), f"{prefix}.sha256 is invalid", errors)
            add_if_false(bool(item.get("kind")), f"{prefix}.kind is required", errors)
            if raw_path and digest:
                path = resolve_recorded_path(raw_path, manifest_path.parent)
                registered_sources.add((raw_path, digest))
                if path.is_file():
                    input_realpaths.add(path.resolve())
                    add_if_false(sha256_file(path) == digest, f"{prefix} hash mismatch", errors)
                else:
                    errors.append(f"{prefix} file not found: {raw_path}")

    outputs = manifest.get("outputs")
    add_if_false(isinstance(outputs, dict), "outputs must be an object", errors)
    output_realpaths: set[Path] = set()
    if isinstance(outputs, dict):
        for name, item in outputs.items():
            prefix = f"outputs.{name}"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            raw_path, digest = item.get("path"), item.get("sha256")
            add_if_false(bool(raw_path), f"{prefix}.path is required", errors)
            add_if_false(bool(SHA_RE.match(str(digest or ""))), f"{prefix}.sha256 is invalid", errors)
            if raw_path and digest:
                path = resolve_recorded_path(raw_path, manifest_path.parent)
                if path.is_file():
                    real = path.resolve()
                    output_realpaths.add(real)
                    add_if_false(sha256_file(path) == digest, f"{prefix} hash mismatch", errors)
                    registered_sources.add((raw_path, digest))
                else:
                    errors.append(f"{prefix} file not found: {raw_path}")
    overlap = input_realpaths & output_realpaths
    add_if_false(not overlap, f"outputs overwrite inputs: {sorted(map(str, overlap))}", errors)
    add_if_false(manifest_path.resolve() not in input_realpaths, "generation manifest cannot be an input", errors)

    unresolved_items = unresolved.get("items")
    add_if_false(isinstance(unresolved_items, list), "unresolved.items must be an array", errors)
    unresolved_index: dict[str, dict[str, Any]] = {}
    if isinstance(unresolved_items, list):
        for index, item in enumerate(unresolved_items):
            prefix = f"unresolved.items[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            item_id = item.get("item_id")
            add_if_false(bool(item_id), f"{prefix}.item_id is required", errors)
            if item_id:
                add_if_false(item_id not in unresolved_index, f"duplicate unresolved item: {item_id}", errors)
                unresolved_index[item_id] = item
            for key in ("metric_item", "section", "reason", "requested_from", "question"):
                add_if_false(bool(str(item.get(key, "")).strip()), f"{prefix}.{key} is required", errors)
            add_if_false(isinstance(item.get("blocks_strict_output"), bool), f"{prefix}.blocks_strict_output must be boolean", errors)

    declared_ids = manifest.get("unresolved_item_ids")
    add_if_false(isinstance(declared_ids, list), "unresolved_item_ids must be an array", errors)
    if isinstance(declared_ids, list):
        add_if_false(declared_ids == list(unresolved_index), "unresolved_item_ids must exactly match unresolved file order", errors)

    facts = manifest.get("facts")
    add_if_false(isinstance(facts, list), "facts must be an array", errors)
    fact_ids: set[str] = set()
    unresolved_fact_ids: set[str] = set()
    if isinstance(facts, list):
        for index, fact in enumerate(facts):
            prefix = f"facts[{index}]"
            if not isinstance(fact, dict):
                errors.append(f"{prefix} must be an object")
                continue
            fact_id, state = fact.get("fact_id"), fact.get("status")
            add_if_false(bool(fact_id), f"{prefix}.fact_id is required", errors)
            if fact_id:
                add_if_false(fact_id not in fact_ids, f"duplicate fact_id: {fact_id}", errors)
                fact_ids.add(fact_id)
            add_if_false(state in FACT_STATES, f"{prefix}.status is invalid", errors)
            sections = fact.get("used_in_sections")
            add_if_false(isinstance(sections, list), f"{prefix}.used_in_sections must be an array", errors)
            if state == "UNRESOLVED":
                unresolved_fact_ids.add(str(fact_id))
                for key in ("value", "source_path", "source_sha256", "locator"):
                    add_if_false(fact.get(key) is None, f"{prefix}.{key} must be null when unresolved", errors)
                add_if_false(fact_id in unresolved_index, f"{prefix} has no matching unresolved item", errors)
            elif state in FACT_STATES:
                source_key = (fact.get("source_path"), fact.get("source_sha256"))
                add_if_false(source_key in registered_sources, f"{prefix} source is not registered", errors)
                add_if_false(bool(fact.get("locator")), f"{prefix}.locator is required", errors)

    extra_unresolved = set(unresolved_index) - unresolved_fact_ids
    add_if_false(not extra_unresolved, f"unresolved items without facts: {sorted(extra_unresolved)}", errors)

    status = manifest.get("status")
    add_if_false(status in RUN_STATES, "manifest status is invalid", errors)
    technical_errors = manifest.get("errors")
    add_if_false(isinstance(technical_errors, list), "manifest errors must be an array", errors)
    blockers = [item for item in unresolved_index.values() if item.get("blocks_strict_output")]
    document_present = isinstance(outputs, dict) and "document" in outputs
    if status == "GENERATED":
        add_if_false(not unresolved_index, "GENERATED requires no unresolved items", errors)
        add_if_false(document_present, "GENERATED requires a document", errors)
        add_if_false(not technical_errors, "GENERATED requires empty errors", errors)
    elif status == "GENERATED_WITH_UNRESOLVED_ITEMS":
        add_if_false(mode == "draft", "GENERATED_WITH_UNRESOLVED_ITEMS requires draft mode", errors)
        add_if_false(bool(unresolved_index), "status requires unresolved items", errors)
        add_if_false(document_present, "status requires a document", errors)
        add_if_false(not technical_errors, "successful draft requires empty errors", errors)
    elif status == "BLOCKED_BY_MISSING_INPUT":
        add_if_false(mode == "strict", "blocked status requires strict mode", errors)
        add_if_false(bool(blockers), "blocked status requires strict blockers", errors)
        add_if_false(not technical_errors, "blocked status is not a technical error", errors)
    elif status == "ERROR":
        add_if_false(bool(technical_errors), "ERROR requires technical errors", errors)

    if mode == "strict":
        blocker_codes = {item.get("metric_item") for item in blockers}
        expected_present = DEFAULT_BLOCKERS & {item.get("metric_item") for item in unresolved_index.values()}
        add_if_false(expected_present <= blocker_codes, "default strict unresolved items must block output", errors)
        if blockers:
            add_if_false(status == "BLOCKED_BY_MISSING_INPUT", "strict blockers require blocked status", errors)

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--unresolved", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = load_object(args.manifest, "generation manifest")
        unresolved = load_object(args.unresolved, "unresolved items")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors = validate(manifest, unresolved, args.manifest)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

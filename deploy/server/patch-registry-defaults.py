"""Rewrite UIE path defaults in the BG-06-01 registry descriptor (build time).

The committed descriptor
(configs/registry/calculators/bg-06-01-sensitive-entity-detection-v1.0.0.json)
carries the developer Mac's absolute paths as parameter defaults:

    ner_python -> /Users/daphne/miniconda3/envs/ner-eval/bin/python
    model_dir  -> /Users/daphne/Dev/.../experiments/bg06_ner/models/uie-mini
    vendor_src -> /Users/daphne/Dev/.../experiments/bg06_ner/vendor/uie_pytorch/src

This script rewrites those defaults to the server-container paths inside the
image copy of the file only (the repository source file is never touched).
At runtime MetricRegistryService.sync() upserts descriptors from these JSON
files into the database, so the corrected defaults reach both fresh and
migrated databases on every boot.

model_checksum is intentionally left untouched: the migrated weight file is a
byte-identical copy, so the sha256 must stay the same (and a mismatch would be
a genuine error we want to surface, not paper over).

Usage (run from the repo root, see Dockerfile.api):
    python deploy/server/patch-registry-defaults.py \
        --registry configs/registry \
        --ner-python /opt/ner-venv/bin/python \
        --model-dir /mnt/qa-eval-models/uie-mini \
        --vendor-src /mnt/qa-eval-vendor/uie_pytorch_src
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DESCRIPTOR_REL = Path("calculators") / "bg-06-01-sensitive-entity-detection-v1.0.0.json"
CALCULATOR_KEY = "bg_06_01_sensitive_entity_detection"
PATH_KEYS = ("ner_python", "model_dir", "vendor_src")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--ner-python", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--vendor-src", required=True)
    args = parser.parse_args()

    descriptor_path = args.registry / DESCRIPTOR_REL
    if not descriptor_path.is_file():
        print(f"FATAL: descriptor not found: {descriptor_path}", file=sys.stderr)
        return 1

    document = json.loads(descriptor_path.read_text(encoding="utf-8"))
    if document.get("calculator_key") != CALCULATOR_KEY:
        print(
            f"FATAL: unexpected calculator_key {document.get('calculator_key')!r}",
            file=sys.stderr,
        )
        return 1

    properties = document["parameter_schema"]["properties"]
    before = {key: properties[key].get("default") for key in PATH_KEYS}
    replacements = {
        "ner_python": args.ner_python,
        "model_dir": args.model_dir,
        "vendor_src": args.vendor_src,
    }
    for key, value in replacements.items():
        if key not in properties:
            print(f"FATAL: parameter {key!r} missing from descriptor", file=sys.stderr)
            return 1
        properties[key]["default"] = value

    checksum = properties["model_checksum"].get("default")
    descriptor_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"patched {descriptor_path}:")
    for key in PATH_KEYS:
        print(f"  {key}: {before[key]} -> {replacements[key]}")
    print(f"  model_checksum: kept ({checksum})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

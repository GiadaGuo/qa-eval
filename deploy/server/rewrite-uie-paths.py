"""One-shot migration: rewrite embedded UIE paths in historical contracts.

Why this exists: when a QualityContractVersion is created, fully-resolved
calculator parameters — including the developer Mac's absolute UIE paths from
the registry defaults — are persisted into quality_contract_versions.document.
Evaluation planning merges those stored parameters over the (already patched)
registry defaults, so merely re-syncing the registry does NOT fix historical
contracts from a migrated database. This script walks every contract document
and rewrites the three path parameters to the server-container paths, then
recomputes the contract fingerprint with the exact formula used at creation
(json.dumps(body-without-fingerprint, default=str, sort_keys, compact separators)
-> sha256), keeping document and the fingerprint column consistent.

Scope is deliberately narrow:
  - only quality_contract_versions.document (+ its fingerprint column)
  - only dict entries whose keys are exactly ner_python / model_dir / vendor_src
  - historical evaluation runs / plans keep their recorded parameters: they
    document what actually ran and are not re-executed.

Idempotent: re-running produces no changes. Read-only dry-run mode: --dry-run.

Run inside the API container (paths rewrite only to the container mount points):
    sudo docker compose --env-file .env exec api python /opt/scripts/rewrite-uie-paths.py --dry-run
    sudo docker compose --env-file .env exec api python /opt/scripts/rewrite-uie-paths.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

NEW_VALUES = {
    "ner_python": "/opt/ner-venv/bin/python",
    "model_dir": "/mnt/qa-eval-models/uie-mini",
    "vendor_src": "/mnt/qa-eval-vendor/uie_pytorch_src",
}
PATH_KEYS = frozenset(NEW_VALUES)


def rewrite(node: Any, changes: list[tuple[str, str, str]]) -> Any:
    """Recursively rewrite the three path keys in-place."""
    if isinstance(node, dict):
        for key in list(node):
            value = node[key]
            if key in PATH_KEYS and isinstance(value, str) and value != NEW_VALUES[key]:
                changes.append((key, value, NEW_VALUES[key]))
                node[key] = NEW_VALUES[key]
            else:
                rewrite(value, changes)
    elif isinstance(node, list):
        for value in node:
            rewrite(value, changes)
    return node


def recompute_fingerprint(document: dict[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "fingerprint"}
    canonical = json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    from sqlalchemy import select

    from qa_eval.infrastructure.db.models.quality_contract import (
        QualityContractVersionModel,
    )
    from qa_eval.infrastructure.db.session import SessionLocal

    total_changes = 0
    total_rows = 0
    with SessionLocal() as session:
        rows = session.scalars(select(QualityContractVersionModel)).all()
        for row in rows:
            changes: list[tuple[str, str, str]] = []
            document = rewrite(row.document, changes)
            if not changes:
                continue
            total_rows += 1
            total_changes += len(changes)
            new_fingerprint = recompute_fingerprint(document)
            print(f"contract {row.contract_version_id} ({row.name}):")
            for key, old, new in changes:
                print(f"  {key}: {old} -> {new}")
            print(f"  fingerprint: {row.fingerprint} -> {new_fingerprint}")
            if not args.dry_run:
                # JSON 列就地修改不会触发 SQLAlchemy 的变更检测（赋回同一对象
                # 等于没变），必须显式打标，否则 commit 静默不写库。
                from sqlalchemy.orm.attributes import flag_modified

                row.document = document
                flag_modified(row, "document")
                row.fingerprint = new_fingerprint
        if args.dry_run:
            print(f"\n[dry-run] would rewrite {total_rows} contract(s), {total_changes} path(s)")
        else:
            session.commit()
            print(f"\nrewrote {total_rows} contract(s), {total_changes} path(s); committed")

    if total_changes == 0:
        print("no embedded UIE paths needed rewriting (already clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

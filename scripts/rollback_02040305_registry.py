"""Safely remove the erroneous 02040305 Registry rows from one SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TARGET_METRIC = ("02040305", "1.0.0")
TARGET_CALCULATOR = ("policy_structure_compliance", "1.0.0")
REGISTRY_TABLES = {
    "metric_definitions",
    "calculator_implementations",
    "metric_calculator_bindings",
}


def _rows(connection: sqlite3.Connection, query: str, parameters=()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def audit(connection: sqlite3.Connection) -> dict[str, Any]:
    metric = _rows(
        connection,
        "SELECT * FROM metric_definitions WHERE metric_key=? AND version=?",
        TARGET_METRIC,
    )
    bindings = _rows(
        connection,
        "SELECT * FROM metric_calculator_bindings "
        "WHERE metric_key=? AND metric_version=?",
        TARGET_METRIC,
    )
    calculator = _rows(
        connection,
        "SELECT * FROM calculator_implementations "
        "WHERE calculator_key=? AND version=?",
        TARGET_CALCULATOR,
    )
    references = []
    tables = _rows(
        connection,
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
    )
    for table in tables:
        name = table["name"]
        columns = {
            row["name"]
            for row in _rows(connection, f'PRAGMA table_info("{name}")')
        }
        if "document" not in columns:
            continue
        count = connection.execute(
            f'SELECT count(*) FROM "{name}" '
            "WHERE CAST(document AS TEXT) LIKE ?",
            (f"%{TARGET_METRIC[0]}%",),
        ).fetchone()[0]
        if count:
            references.append({"table": name, "count": count})
    external_references = [
        item for item in references if item["table"] not in REGISTRY_TABLES
    ]
    return {
        "database": str(Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve()),
        "target": {"metric_key": TARGET_METRIC[0], "version": TARGET_METRIC[1]},
        "metric_definitions": metric,
        "bindings": bindings,
        "shared_calculator": calculator,
        "document_references": references,
        "external_references": external_references,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        parser.error(f"database does not exist: {database}")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    before = audit(connection)
    summary = {"mode": "execute" if args.execute else "dry-run", "before": before}
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    if before["external_references"]:
        raise SystemExit("refusing rollback: non-Registry references exist")
    if args.backup_dir is None:
        parser.error("--backup-dir is required with --execute")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    json_path = backup_dir / f"02040305-registry-{timestamp}.json"
    sqlite_path = backup_dir / f"qa-eval-before-02040305-rollback-{timestamp}.sqlite3"
    json_path.write_text(
        json.dumps(before, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    backup = sqlite3.connect(sqlite_path)
    connection.backup(backup)
    backup.close()

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM metric_calculator_bindings "
            "WHERE metric_key=? AND metric_version=?",
            TARGET_METRIC,
        )
        connection.execute(
            "DELETE FROM metric_definitions WHERE metric_key=? AND version=?",
            TARGET_METRIC,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    after = audit(connection)
    if after["metric_definitions"] or after["bindings"]:
        raise SystemExit("rollback verification failed")
    summary.update(
        {
            "backup": {"registry_json": str(json_path), "database": str(sqlite_path)},
            "after": after,
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

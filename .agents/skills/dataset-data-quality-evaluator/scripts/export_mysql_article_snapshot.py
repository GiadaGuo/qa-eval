"""Export policy.jhsjk_article to an atomic, read-only JSONL snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATABASE = "policy"
TABLE = "jhsjk_article"
ID_FIELD = "id"
TEXT_FIELD = "plain_text_content"
DEFAULT_BATCH_SIZE = 1_000


def _json_line(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sample_id(value: Any) -> str:
    if value is None:
        raise ValueError("NULL_ID")
    if isinstance(value, bytes):
        return value.hex()
    rendered = str(value)
    if not rendered:
        raise ValueError("EMPTY_ID")
    return rendered


def _default_output_dir(now: datetime) -> Path:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("data/dataset-data-quality-evaluator/policy.jhsjk_article") / stamp


def _table_engine(connection: Any, database: str, table: str) -> str | None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT ENGINE FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            (database, table),
        )
        row = cursor.fetchone()
        return None if row is None else str(row[0])
    finally:
        cursor.close()


def export_snapshot(
    reader: Any,
    output_dir: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Export through a project reader without exposing its credentials."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if reader.database != DATABASE or reader.table != TABLE:
        raise ValueError("reader must target policy.jhsjk_article")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent)
    )
    connection = None
    transaction_started = False

    try:
        connection = reader._connect()  # Reuse the project's configured connector.
        connection.start_transaction(
            consistent_snapshot=True,
            isolation_level="REPEATABLE READ",
            readonly=True,
        )
        transaction_started = True
        table_sql = reader._quoted_table()
        id_sql = reader._quote_column(ID_FIELD)
        text_sql = reader._quote_column(TEXT_FIELD)
        engine = _table_engine(connection, reader.database, reader.table)

        count_cursor = connection.cursor()
        try:
            count_cursor.execute(f"SELECT COUNT(*) FROM {table_sql}")
            expected_row_count = int(count_cursor.fetchone()[0])
        finally:
            count_cursor.close()

        snapshot_path = staging_dir / "raw_snapshot.jsonl"
        digest = hashlib.sha256()
        row_count = 0
        last_id: Any = None
        first_page = True

        with snapshot_path.open("wb") as snapshot_file:
            while True:
                cursor = connection.cursor(dictionary=True)
                try:
                    if first_page:
                        sql = (
                            f"SELECT {id_sql}, {text_sql} FROM {table_sql} "
                            f"ORDER BY {id_sql} ASC LIMIT %s"
                        )
                        params = (batch_size,)
                    else:
                        sql = (
                            f"SELECT {id_sql}, {text_sql} FROM {table_sql} "
                            f"WHERE {id_sql} > %s ORDER BY {id_sql} ASC LIMIT %s"
                        )
                        params = (last_id, batch_size)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
                finally:
                    cursor.close()

                if not rows:
                    break
                for row in rows:
                    raw_id = row[ID_FIELD]
                    if last_id is not None and raw_id <= last_id:
                        raise ValueError("id ordering is not strictly increasing")
                    text = row[TEXT_FIELD]
                    if text is not None and not isinstance(text, str):
                        raise TypeError("plain_text_content must be string or null")
                    record = {
                        "sample_id": _sample_id(raw_id),
                        "text": text,
                        "text_is_null": text is None,
                        "source": {
                            "type": "mysql",
                            "database": reader.database,
                            "table": reader.table,
                            "id_field": ID_FIELD,
                            "text_field": TEXT_FIELD,
                        },
                    }
                    encoded = _json_line(record)
                    snapshot_file.write(encoded)
                    digest.update(encoded)
                    row_count += 1
                    last_id = raw_id
                first_page = False
                if len(rows) < batch_size:
                    break
            snapshot_file.flush()
            os.fsync(snapshot_file.fileno())

        if row_count != expected_row_count:
            raise RuntimeError(
                f"row count mismatch: exported={row_count}, expected={expected_row_count}"
            )

        engine_is_innodb = engine is not None and engine.casefold() == "innodb"
        consistency = {
            "transaction": "REPEATABLE READ READ ONLY WITH CONSISTENT SNAPSHOT",
            "table_engine": engine,
            "status": (
                "REPEATABLE_READ_CONSISTENT_SNAPSHOT"
                if engine_is_innodb
                else "LIMITED_NON_TRANSACTIONAL_OR_UNKNOWN_ENGINE"
            ),
            "strong_consistency_claimed": engine_is_innodb,
            "limitations": (
                None
                if engine_is_innodb
                else "The table engine is not confirmed as InnoDB; the transaction may not isolate concurrent writes."
            ),
        }
        export_time = (exported_at or datetime.now(UTC)).astimezone(UTC)
        manifest = {
            "schema_version": "1.0",
            "snapshot_format": "jsonl",
            "source": {
                "type": "mysql",
                "database": reader.database,
                "table": reader.table,
                "identifier": f"{reader.database}.{reader.table}",
            },
            "field_mapping": {
                "sample_id": ID_FIELD,
                "text": TEXT_FIELD,
                "text_is_null": f"{TEXT_FIELD} IS NULL",
                "fallback_field": None,
            },
            "exported_at": export_time.isoformat().replace("+00:00", "Z"),
            "row_count": row_count,
            "source_count_in_same_transaction": expected_row_count,
            "sha256": f"sha256:{digest.hexdigest()}",
            "file": "raw_snapshot.jsonl",
            "ordering": {
                "columns": [ID_FIELD],
                "direction": "ASC",
                "pagination": "keyset",
                "unique_and_strictly_increasing_verified": True,
            },
            "batch_size": batch_size,
            "consistency": consistency,
        }
        manifest_path = staging_dir / "snapshot_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with manifest_path.open("rb") as manifest_file:
            os.fsync(manifest_file.fileno())

        connection.rollback()
        transaction_started = False
        connection.close()
        connection = None
        staging_dir.rename(output_dir)
        return manifest
    except Exception:
        if connection is not None and transaction_started:
            connection.rollback()
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    finally:
        if connection is not None:
            connection.close()


def _project_reader() -> Any:
    # Reuse the existing credential/configuration path. The deferred import
    # keeps unit tests from loading .env or attempting a connection.
    repository_root = Path(__file__).resolve().parents[4]
    repository_root_text = str(repository_root)
    if repository_root_text not in sys.path:
        sys.path.insert(0, repository_root_text)

    from apps.api.dependencies import get_mysql_reader

    return get_mysql_reader()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export policy.jhsjk_article as an atomic JSONL snapshot."
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)
    output_dir = args.output_dir or _default_output_dir(now)
    try:
        manifest = export_snapshot(
            _project_reader(),
            output_dir,
            batch_size=args.batch_size,
            exported_at=now,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary hides credentials.
        print(f"snapshot export failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "row_count": manifest["row_count"],
                "sha256": manifest["sha256"],
                "consistency_status": manifest["consistency"]["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

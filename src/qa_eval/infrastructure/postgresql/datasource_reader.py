"""PostgreSQL source discovery and repeatable-read snapshots."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import IsolationLevel, sql
from psycopg.rows import dict_row

from qa_eval.application.normalization.source_readers import json_compatible


class PostgresDataSourceReader:
    def __init__(self, *, host: str, port: int, user: str, password: str, database: str, table: str | None):
        self.connection_config = {"host": host, "port": port, "user": user, "password": password, "dbname": database}
        self.database = database
        self.table = table

    def _connect(self):
        connection = psycopg.connect(
            **self.connection_config,
            connect_timeout=5,
            sslmode="require",
            options="-c statement_timeout=60000 -c lock_timeout=5000",
            row_factory=dict_row,
        )
        connection.read_only = True
        connection.isolation_level = IsolationLevel.REPEATABLE_READ
        return connection

    def list_snapshot_tables(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT n.nspname AS schema, c.relname AS name
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                AND n.nspname NOT LIKE 'pg_%'
                AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')
                AND position('.' IN n.nspname) = 0 AND position('.' IN c.relname) = 0
                ORDER BY n.nspname, c.relname LIMIT 500"""
            )
            return [f"{row['schema']}.{row['name']}" for row in rows]

    def _schema(self, connection):
        schema, name = self.table.split(".", 1)
        identifier = sql.Identifier(schema, name)
        connection.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(identifier))
        fields = connection.execute(
            """SELECT a.attname AS name, pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                NOT a.attnotnull AS nullable,
                EXISTS (SELECT 1 FROM pg_catalog.pg_index i
                    WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY(i.indkey)) AS is_primary_key
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
                AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
            (schema, name),
        ).fetchall()
        if not fields:
            raise ValueError("请选择可访问的 PostgreSQL 数据表")
        return identifier, fields

    def get_preview(self, limit: int = 5, *, readonly: bool = True) -> dict:
        with self._connect() as connection:
            identifier, fields = self._schema(connection)
            rows = connection.execute(sql.SQL("SELECT * FROM {} LIMIT %s").format(identifier), (limit,)).fetchall()
            return {
                "fields": fields,
                "rows": [
                    {key: value[:300] if isinstance(value, str) else value for key, value in json_compatible(row).items()}
                    for row in rows
                ],
            }

    def write_snapshot(
        self, path: Path, on_progress: Callable[[int], None] | None = None,
    ) -> tuple[datetime, list[dict[str, Any]]]:
        with self._connect() as connection:
            identifier, fields = self._schema(connection)
            captured_at = datetime.now(UTC)
            with connection.cursor(name="qa_eval_dataset_snapshot") as cursor, path.open("w", encoding="utf-8") as output:
                cursor.execute(sql.SQL("SELECT * FROM {}").format(identifier))
                for count, row in enumerate(cursor, 1):
                    output.write(json.dumps(json_compatible(row), ensure_ascii=False, allow_nan=False) + "\n")
                    if on_progress:
                        on_progress(count)
        return captured_at, fields

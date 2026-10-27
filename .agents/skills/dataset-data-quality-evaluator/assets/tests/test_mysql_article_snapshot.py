from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "export_mysql_article_snapshot.py"
SPEC = importlib.util.spec_from_file_location(
    "export_mysql_article_snapshot", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeCursor:
    def __init__(self, connection, dictionary=False):
        self.connection = connection
        self.dictionary = dictionary
        self.result = []

    def execute(self, sql, params=None):
        self.connection.queries.append((sql, params))
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self.result = [(self.connection.engine,)]
        elif "COUNT(*)" in sql:
            self.result = [(len(self.connection.rows),)]
        elif sql.startswith("SELECT `id`, `plain_text_content`"):
            if self.connection.fail_on_page == self.connection.page_count:
                raise RuntimeError("anonymous fixture interruption")
            self.connection.page_count += 1
            if "WHERE" in sql:
                last_id, limit = params
                rows = [row for row in self.connection.rows if row["id"] > last_id]
            else:
                (limit,) = params
                rows = self.connection.rows
            self.result = [
                dict(row) for row in sorted(rows, key=lambda row: row["id"])[:limit]
            ]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows, *, engine="InnoDB", fail_on_page=None):
        self.rows = rows
        self.engine = engine
        self.fail_on_page = fail_on_page
        self.page_count = 0
        self.queries = []
        self.transaction_kwargs = None
        self.rollback_count = 0
        self.closed = False

    def start_transaction(self, **kwargs):
        self.transaction_kwargs = kwargs

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary=dictionary)

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


class FakeReader:
    database = "policy"
    table = "jhsjk_article"

    def __init__(self, connection):
        self.connection = connection

    def _connect(self):
        return self.connection

    def _quoted_table(self):
        return "`policy`.`jhsjk_article`"

    @staticmethod
    def _quote_column(name):
        return f"`{name}`"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def export(tmp_path, rows, *, batch_size=2, **connection_kwargs):
    output_dir = tmp_path / "snapshot"
    connection = FakeConnection(rows, **connection_kwargs)
    manifest = MODULE.export_snapshot(
        FakeReader(connection),
        output_dir,
        batch_size=batch_size,
        exported_at=datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
    )
    return manifest, output_dir, connection


def test_keyset_pagination_stable_sort_count_and_hash(tmp_path):
    rows = [
        {"id": "003", "plain_text_content": "third"},
        {"id": "001", "plain_text_content": "first"},
        {"id": "002", "plain_text_content": "second"},
    ]
    manifest, output_dir, connection = export(tmp_path, rows, batch_size=2)

    snapshot = output_dir / "raw_snapshot.jsonl"
    records = read_jsonl(snapshot)
    assert [record["sample_id"] for record in records] == ["001", "002", "003"]
    assert manifest["row_count"] == 3
    assert manifest["source_count_in_same_transaction"] == 3
    expected_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert manifest["sha256"] == f"sha256:{expected_hash}"
    assert connection.page_count == 2
    assert connection.transaction_kwargs == {
        "consistent_snapshot": True,
        "isolation_level": "REPEATABLE READ",
        "readonly": True,
    }
    assert connection.rollback_count == 1
    assert connection.closed is True
    assert any("WHERE `id` > %s" in query for query, _ in connection.queries)


def test_null_text_is_preserved_without_content_fallback(tmp_path):
    manifest, output_dir, _ = export(
        tmp_path,
        [{"id": "001", "plain_text_content": None}],
    )

    [record] = read_jsonl(output_dir / "raw_snapshot.jsonl")
    assert record["text"] is None
    assert record["text_is_null"] is True
    assert "content" not in record
    assert manifest["field_mapping"]["fallback_field"] is None


def test_manifest_records_limited_consistency_for_non_innodb(tmp_path):
    manifest, _, _ = export(
        tmp_path,
        [{"id": "001", "plain_text_content": "anonymous"}],
        engine="MyISAM",
    )

    consistency = manifest["consistency"]
    assert consistency["strong_consistency_claimed"] is False
    assert consistency["status"] == "LIMITED_NON_TRANSACTIONAL_OR_UNKNOWN_ENGINE"
    assert consistency["limitations"]


def test_duplicate_or_unstable_id_fails_without_success_snapshot(tmp_path):
    output_dir = tmp_path / "snapshot"
    connection = FakeConnection(
        [
            {"id": "001", "plain_text_content": "one"},
            {"id": "001", "plain_text_content": "duplicate id"},
        ]
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        MODULE.export_snapshot(FakeReader(connection), output_dir, batch_size=5)

    assert not output_dir.exists()
    assert list(tmp_path.glob(".snapshot.tmp-*")) == []
    assert connection.rollback_count == 1


def test_interruption_removes_staging_and_never_publishes_manifest(tmp_path):
    output_dir = tmp_path / "snapshot"
    connection = FakeConnection(
        [
            {"id": "001", "plain_text_content": "one"},
            {"id": "002", "plain_text_content": "two"},
            {"id": "003", "plain_text_content": "three"},
        ],
        fail_on_page=1,
    )

    with pytest.raises(RuntimeError, match="fixture interruption"):
        MODULE.export_snapshot(FakeReader(connection), output_dir, batch_size=2)

    assert not output_dir.exists()
    assert list(tmp_path.glob(".snapshot.tmp-*")) == []
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_project_reader_bootstraps_repository_import_path(monkeypatch):
    repository_root = str(SCRIPT_PATH.resolve().parents[4])
    monkeypatch.setattr(
        sys, "path", [item for item in sys.path if item != repository_root]
    )
    fake_reader = object()
    dependencies = types.ModuleType("apps.api.dependencies")
    dependencies.get_mysql_reader = lambda: fake_reader
    monkeypatch.setitem(sys.modules, "apps.api.dependencies", dependencies)

    assert MODULE._project_reader() is fake_reader
    assert sys.path[0] == repository_root

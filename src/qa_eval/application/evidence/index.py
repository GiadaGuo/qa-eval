from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from qa_eval.application.normalization.record_reader import (
    RawJsonlRecordReader,
    RawRecordManifest,
    RawSampleRecord,
)
from qa_eval.domain.ports.artifact_store import ArtifactStore

IndexStatus = Literal["NOT_BUILT", "BUILDING", "READY", "FAILED", "STALE"]


@dataclass(frozen=True)
class ArtifactIndexSpec:
    source_uri: str
    source_checksum: str
    key_field: str
    index_kind: Literal["EVIDENCE", "SAMPLE"]

    raw_manifest: RawRecordManifest | None = None
    decision_filters: bool = False

    def __post_init__(self) -> None:
        if self.raw_manifest is not None and (
            self.index_kind != "SAMPLE"
            or self.key_field != "sample_id"
            or self.source_uri != self.raw_manifest.source_uri
            or self.source_checksum != self.raw_manifest.source_sha256
        ):
            raise ValueError("raw index source does not match Manifest")

    @property
    def identity(self) -> str:
        checksum = self.source_checksum.removeprefix("sha256:")
        if self.raw_manifest is not None:
            version = self.raw_manifest.dataset_version_id
            return f"sample-v3-jsonl-1-{version}-{checksum}"
        prefix = "evidence-v2" if self.decision_filters else self.index_kind.lower()
        return f"{prefix}-{checksum}"


class IndexNotReadyError(RuntimeError):
    def __init__(self, status: IndexStatus, message: str):
        super().__init__(message)
        self.status = status


class ArtifactRecordIndexService:
    """Rebuildable SQLite byte-offset indexes for immutable JSONL artifacts."""

    _locks_guard = threading.Lock()
    _locks: ClassVar[dict[str, threading.Lock]] = {}

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    def status(self, spec: ArtifactIndexSpec) -> dict[str, Any]:
        metadata_uri = self._metadata_uri(spec)
        if not self.artifact_store.exists(metadata_uri):
            return self._metadata(spec, "NOT_BUILT")
        metadata = json.loads(self.artifact_store.read(metadata_uri))
        if metadata.get("source_checksum") != spec.source_checksum:
            return {**metadata, "status": "STALE"}
        if metadata.get("status") == "READY" and not self.artifact_store.exists(
            self._index_uri(spec)
        ):
            return {**metadata, "status": "STALE"}
        return metadata

    def build(self, spec: ArtifactIndexSpec, *, force: bool = False) -> dict[str, Any]:
        lock = self._lock(spec.identity)
        if not lock.acquire(blocking=False):
            return self._metadata(spec, "BUILDING")
        try:
            current = self.status(spec)
            if current["status"] == "READY" and not force:
                if spec.decision_filters and "decision_summary" not in current:
                    with self._connection(spec) as connection:
                        current["decision_summary"] = self._summarize_decisions(connection)
                    self._write_metadata(current)
                return current
            self._write_metadata(self._metadata(spec, "BUILDING"))
            try:
                metadata = self._build_locked(spec)
            except Exception as exc:
                failed = self._metadata(
                    spec,
                    "FAILED",
                    error={"code": "INDEX_BUILD_FAILED", "message": str(exc)},
                )
                self._write_metadata(failed)
                raise
            self._write_metadata(metadata)
            return metadata
        finally:
            lock.release()

    def query(
        self,
        spec: ArtifactIndexSpec,
        *,
        limit: int,
        cursor: str | None = None,
        row_id: str | None = None,
        sample_id: str | None = None,
        content_id: str | None = None,
        fact_code: str | None = None,
        status_filter: str | None = None,
        reason: str | None = None,
        unique_samples: bool = False,
    ) -> tuple[list[dict[str, Any] | RawSampleRecord], str | None]:
        metadata = self.status(spec)
        status = metadata["status"]
        if status != "READY":
            raise IndexNotReadyError(status, f"artifact index is {status}")
        after_key = self._decode_cursor(cursor) if cursor else None
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("record_key", row_id),
            ("sample_id", sample_id),
            ("content_id", content_id),
            ("fact_code", fact_code),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if status_filter is not None or reason is not None:
            if not spec.decision_filters:
                raise ValueError("decision filters require an evidence index")
            if status_filter is not None:
                clauses.append("decision_status = ?")
                values.append(status_filter)
            if reason is not None:
                clauses.append("record_key IN (SELECT record_key FROM reasons WHERE reason = ?)")
                values.append(reason)
        if unique_samples:
            if not spec.decision_filters:
                raise ValueError("sample deduplication requires a Decision index")
            selected = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            clauses = [
                "record_key IN (SELECT MIN(record_key) FROM records "
                f"{selected} GROUP BY COALESCE(sample_id, record_key))"
            ]
        if after_key is not None:
            clauses.append("record_key > ?")
            values.append(after_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row_digest = "row_digest" if spec.raw_manifest else "NULL"
        sql = (
            f"SELECT record_key, byte_start, byte_length, line_no, {row_digest} FROM records "
            f"{where} ORDER BY record_key LIMIT ?"
        )
        values.append(limit + 1)
        with self._index_path(spec) as index_path, closing(
            sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            indexed = connection.execute(sql, values).fetchall()
        has_more = len(indexed) > limit
        indexed = indexed[:limit]
        reader = RawJsonlRecordReader(spec.raw_manifest) if spec.raw_manifest else None
        rows: list[dict[str, Any] | RawSampleRecord] = []
        for key, start, length, line_no, expected_digest in indexed:
            line = self.artifact_store.read_range(spec.source_uri, start, length)
            if len(line) != length:
                raise ValueError("indexed record byte range is incomplete")
            if reader is None:
                rows.append(json.loads(line))
                continue
            if hashlib.sha256(line).digest() != expected_digest:
                raise ValueError("indexed record checksum mismatch")
            record = reader.decode(line, start=start, number=line_no)
            if str(record.id) != key:
                raise ValueError("indexed sample does not match the record identity")
            rows.append(record)
        next_cursor = (
            self._encode_cursor(indexed[-1][0]) if has_more and indexed else None
        )
        return rows, next_cursor

    def decision_summary(self, spec: ArtifactIndexSpec) -> dict[str, Any]:
        if not spec.decision_filters:
            raise ValueError("summary requires a Decision index")
        metadata = self.status(spec)
        status = metadata["status"]
        if status != "READY":
            raise IndexNotReadyError(status, f"artifact index is {status}")
        if "decision_summary" not in metadata:
            raise IndexNotReadyError("STALE", "判定统计尚未准备，请更新现有证据索引")
        return metadata["decision_summary"]

    def decision_preview(
        self, spec: ArtifactIndexSpec, reason: str | None, *, plan: bool = False, group: str | None = None
    ) -> list[dict]:
        summary = self.decision_summary(spec)
        categories = [item for item in summary["reasons"] if reason is None or item["code"] == reason]
        if reason is not None and not categories:
            raise ValueError("未找到该未判定原因")
        # Allocate one sample to every category in rank order before allocating extras.
        limits: dict[str, int] = {}
        budget = 50
        for count in range(1, 4):
            for item in categories:
                if budget and item["records"] >= count:
                    limits[item["code"]] = count
                    budget -= 1
        if plan:
            return [{"code": code, "sample_count": limit} for code, limit in limits.items()]
        if group is not None:
            limits = {group: limits[group]} if group in limits else {}
        with self._connection(spec) as connection:
            groups = []
            for code, limit in limits.items():
                offsets = connection.execute(
                    """SELECT r.byte_start, r.byte_length
                       FROM reasons AS reason CROSS JOIN records AS r
                       WHERE reason.reason = ? AND r.record_key = reason.record_key
                         AND r.decision_status = 'NOT_EVALUATED'
                         AND NOT EXISTS (
                            SELECT 1 FROM records AS earlier
                            JOIN reasons AS matched ON matched.record_key = earlier.record_key
                            WHERE earlier.sample_id = r.sample_id
                              AND earlier.record_key < r.record_key
                              AND earlier.decision_status = r.decision_status
                              AND matched.reason = reason.reason
                         )
                       ORDER BY reason.record_key LIMIT ?""",
                    (code, limit),
                ).fetchall()
                groups.append({"code": code, "rows": [
                    json.loads(self.artifact_store.read_range(spec.source_uri, start, length))
                    for start, length in offsets
                ]})
        return groups

    def iter_evidence_export(self, spec: ArtifactIndexSpec, *, decision_status: str | None = None):
        status = self.status(spec)["status"]
        if status != "READY":
            raise IndexNotReadyError(status, f"artifact index is {status}")
        local_path = getattr(self.artifact_store, "local_read_path", None)
        source = local_path(spec.source_uri).open("rb") if local_path else nullcontext(None)
        with source as local, self._connection(spec) as connection:
            query = "SELECT byte_start, byte_length FROM records"
            params = ()
            if decision_status is not None:
                query += " WHERE decision_status = ?"
                params = (decision_status,)
            for start, length in connection.execute(query + " ORDER BY byte_start", params):
                if local is None:
                    line = self.artifact_store.read_range(spec.source_uri, start, length)
                else:
                    local.seek(start)
                    line = local.read(length)
                yield json.loads(line)

    def iter_decision_export(self, spec: ArtifactIndexSpec, reason: str | None):
        status = self.status(spec)["status"]
        if status != "READY":
            raise IndexNotReadyError(status, f"artifact index is {status}")
        with self._connection(spec) as connection:
            connection.execute("PRAGMA temp_store = FILE")
            if reason is None:
                sql = """SELECT sample, GROUP_CONCAT(DISTINCT reason)
                         FROM (
                           SELECT COALESCE(r.sample_id, r.record_key) AS sample, m.reason
                           FROM reasons AS m CROSS JOIN records AS r
                           WHERE r.record_key = m.record_key AND r.decision_status = 'NOT_EVALUATED'
                           UNION ALL
                           SELECT COALESCE(sample_id, record_key), NULL
                           FROM records WHERE decision_status = 'NOT_EVALUATED'
                         ) GROUP BY sample"""
                yield from connection.execute(sql)
            else:
                sql = """SELECT DISTINCT COALESCE(r.sample_id, r.record_key), m.reason
                         FROM reasons AS m CROSS JOIN records AS r
                         WHERE m.reason = ? AND r.record_key = m.record_key
                           AND r.decision_status = 'NOT_EVALUATED'
                         ORDER BY 1"""
                yield from connection.execute(sql, (reason,))

    @contextmanager
    def _connection(self, spec: ArtifactIndexSpec):
        with self._index_path(spec) as path, closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            yield connection

    @staticmethod
    def _summarize_decisions(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute("PRAGMA temp_store = FILE")
        total, undecided = connection.execute(
            """SELECT COUNT(DISTINCT COALESCE(sample_id, record_key)),
               COUNT(DISTINCT CASE WHEN decision_status = 'NOT_EVALUATED'
                   THEN COALESCE(sample_id, record_key) END)
               FROM records"""
        ).fetchone()
        reasons = connection.execute(
            """SELECT reasons.reason, COUNT(DISTINCT COALESCE(records.sample_id, records.record_key))
               FROM reasons JOIN records USING (record_key)
               WHERE decision_status = 'NOT_EVALUATED'
               GROUP BY reasons.reason ORDER BY 2 DESC, 1"""
        ).fetchall()
        return {
            "total": total,
            "undecided": undecided,
            "reasons": [{"code": code, "records": count} for code, count in reasons],
        }

    @contextmanager
    def _index_path(self, spec: ArtifactIndexSpec):
        local_path = getattr(self.artifact_store, "local_read_path", None)
        if local_path is not None:
            yield local_path(self._index_uri(spec))
            return
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
            path = Path(temporary.name)
            self.artifact_store.materialize(self._index_uri(spec), path)
            yield path

    def get(
        self, spec: ArtifactIndexSpec, record_key: str
    ) -> dict[str, Any] | RawSampleRecord | None:
        rows, _ = self.query(spec, limit=1, row_id=record_key)
        return rows[0] if rows else None

    def _build_locked(self, spec: ArtifactIndexSpec) -> dict[str, Any]:
        expected = spec.source_checksum.removeprefix("sha256:")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.jsonl"
            index_path = root / "records.sqlite3"
            local_path = getattr(self.artifact_store, "local_read_path", None)
            if local_path is not None:
                source_path = local_path(spec.source_uri)
            else:
                self.artifact_store.materialize(spec.source_uri, source_path)
            digest = hashlib.sha256()
            count = 0
            with sqlite3.connect(index_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE records (
                        record_key TEXT PRIMARY KEY,
                        line_no INTEGER NOT NULL,
                        byte_start INTEGER NOT NULL,
                        byte_length INTEGER NOT NULL,
                        sample_id TEXT,
                        content_id TEXT,
                        fact_code TEXT,
                        row_digest BLOB,
                        decision_status TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX ix_records_sample_id ON records(sample_id)"
                )
                if spec.decision_filters:
                    connection.execute("CREATE INDEX ix_decision_status ON records(decision_status, record_key)")
                    connection.execute(
                        "CREATE TABLE reasons (reason TEXT, record_key TEXT, "
                        "PRIMARY KEY (reason, record_key)) WITHOUT ROWID"
                    )
                with source_path.open("rb") as source:
                    reader = (
                        RawJsonlRecordReader(spec.raw_manifest) if spec.raw_manifest else None
                    )
                    line_no = 0
                    for physical_line, line in enumerate(iter(source.readline, b""), 1):
                        start = source.tell() - len(line)
                        digest.update(line)
                        if not line.strip():
                            continue
                        line_no += 1
                        if reader is None:
                            row = json.loads(line)
                        else:
                            record = reader.decode(line, start=start, number=physical_line)
                            row = {"sample_id": str(record.id)}
                            line_no = physical_line
                        key = row.get(spec.key_field)
                        if not isinstance(key, str) or not key:
                            raise ValueError(
                                f"JSONL row {line_no} lacks {spec.key_field}"
                            )
                        connection.execute(
                            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                key,
                                line_no,
                                start,
                                len(line),
                                row.get("sample_id"),
                                row.get("content_id"),
                                row.get("category") or row.get("fact_code"),
                                hashlib.sha256(line).digest() if reader else None,
                                row.get("status") if spec.decision_filters else None,
                            ),
                        )
                        if spec.decision_filters and isinstance(row.get("status"), str):
                            reasons = set()
                            if isinstance(row.get("reason"), str):
                                reasons.update(row["reason"].split("|"))
                            attributes = row.get("attributes")
                            if isinstance(attributes, dict):
                                reasons.update(
                                    reason for reason in attributes.get("reasons", [])
                                    if isinstance(reason, str)
                                )
                                for segment in attributes.get("segments", []):
                                    if not isinstance(segment, dict):
                                        continue
                                    for rule in segment.get("rules", []):
                                        if isinstance(rule, dict) and rule.get("id") and rule.get("reason"):
                                            reasons.add(f"{rule['id']}/{rule['reason']}")
                            connection.executemany(
                                "INSERT INTO reasons VALUES (?, ?)",
                                ((reason, key) for reason in reasons if reason),
                            )
                        count += 1
                connection.commit()
                extra = {"decision_summary": self._summarize_decisions(connection)} if spec.decision_filters else {}
            if digest.hexdigest() != expected:
                raise ValueError("source artifact checksum mismatch")
            stored = self.artifact_store.put_file(self._index_uri(spec), index_path)
        return self._metadata(
            spec,
            "READY",
            record_count=count,
            index_checksum=f"sha256:{stored.sha256}",
            **extra,
        )

    def _metadata(
        self,
        spec: ArtifactIndexSpec,
        status: IndexStatus,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "index_id": spec.identity,
            "index_kind": spec.index_kind,
            "status": status,
            "source_uri": spec.source_uri,
            "source_checksum": spec.source_checksum,
            "key_field": spec.key_field,
            "index_uri": self._index_uri(spec),
            "metadata_uri": self._metadata_uri(spec),
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        }

    def _write_metadata(self, metadata: dict[str, Any]) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as file:
            path = Path(file.name)
            json.dump(metadata, file, ensure_ascii=False, sort_keys=True)
        try:
            self.artifact_store.put_file(metadata["metadata_uri"], path)
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _lock(identity: str) -> threading.Lock:
        with ArtifactRecordIndexService._locks_guard:
            return ArtifactRecordIndexService._locks.setdefault(
                identity, threading.Lock()
            )

    @staticmethod
    def _encode_cursor(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        try:
            return base64.b64decode(
                value + padding, altchars=b"-_", validate=True
            ).decode()
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid index cursor") from exc

    @staticmethod
    def _metadata_uri(spec: ArtifactIndexSpec) -> str:
        return f"{spec.source_uri}.qa-index/{spec.identity}/metadata.json"

    @staticmethod
    def _index_uri(spec: ArtifactIndexSpec) -> str:
        return f"{spec.source_uri}.qa-index/{spec.identity}/records.sqlite3"

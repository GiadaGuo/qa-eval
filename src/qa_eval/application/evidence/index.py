from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from qa_eval.domain.ports.artifact_store import ArtifactStore

IndexStatus = Literal["NOT_BUILT", "BUILDING", "READY", "FAILED", "STALE"]


@dataclass(frozen=True)
class ArtifactIndexSpec:
    source_uri: str
    source_checksum: str
    key_field: str
    index_kind: Literal["EVIDENCE", "SAMPLE"]

    @property
    def identity(self) -> str:
        checksum = self.source_checksum.removeprefix("sha256:")
        return f"{self.index_kind.lower()}-{checksum}"


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
    ) -> tuple[list[dict[str, Any]], str | None]:
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
        if after_key is not None:
            clauses.append("record_key > ?")
            values.append(after_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT record_key, byte_start, byte_length FROM records "
            f"{where} ORDER BY record_key LIMIT ?"
        )
        values.append(limit + 1)
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
            index_path = Path(temporary.name)
            self.artifact_store.materialize(self._index_uri(spec), index_path)
            with sqlite3.connect(index_path) as connection:
                indexed = connection.execute(sql, values).fetchall()
        has_more = len(indexed) > limit
        indexed = indexed[:limit]
        rows = [
            json.loads(self.artifact_store.read_range(spec.source_uri, start, length))
            for _, start, length in indexed
        ]
        next_cursor = (
            self._encode_cursor(indexed[-1][0]) if has_more and indexed else None
        )
        return rows, next_cursor

    def get(self, spec: ArtifactIndexSpec, record_key: str) -> dict[str, Any] | None:
        rows, _ = self.query(spec, limit=1, row_id=record_key)
        return rows[0] if rows else None

    def _build_locked(self, spec: ArtifactIndexSpec) -> dict[str, Any]:
        expected = spec.source_checksum.removeprefix("sha256:")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.jsonl"
            index_path = root / "records.sqlite3"
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
                        fact_code TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX ix_records_sample_id ON records(sample_id)"
                )
                with source_path.open("rb") as source:
                    line_no = 0
                    while line := source.readline():
                        start = source.tell() - len(line)
                        digest.update(line)
                        if not line.strip():
                            continue
                        line_no += 1
                        row = json.loads(line)
                        key = row.get(spec.key_field)
                        if not isinstance(key, str) or not key:
                            raise ValueError(
                                f"JSONL row {line_no} lacks {spec.key_field}"
                            )
                        connection.execute(
                            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                key,
                                line_no,
                                start,
                                len(line),
                                row.get("sample_id"),
                                row.get("content_id"),
                                row.get("category") or row.get("fact_code"),
                            ),
                        )
                        count += 1
                connection.commit()
            if digest.hexdigest() != expected:
                raise ValueError("source artifact checksum mismatch")
            stored = self.artifact_store.put_file(self._index_uri(spec), index_path)
        return self._metadata(
            spec,
            "READY",
            record_count=count,
            index_checksum=f"sha256:{stored.sha256}",
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

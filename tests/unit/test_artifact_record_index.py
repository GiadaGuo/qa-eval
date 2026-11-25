import hashlib
import io
import json
import threading

import pytest

from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
    IndexNotReadyError,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)
from qa_eval.infrastructure.storage.s3_artifact_store import S3ArtifactStore


def _source(store, rows):
    relative = "source/rows.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return store.commit(relative)


def test_index_lifecycle_cursor_filter_and_range_read(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    rows = [
        {
            "row_id": f"row-{index}",
            "sample_id": "sample-a" if index < 2 else "sample-b",
            "content_id": "body",
            "category": "PHONE",
            "value": index,
        }
        for index in range(3)
    ]
    stored = _source(store, rows)
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{stored.sha256}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    assert service.status(spec)["status"] == "NOT_BUILT"
    with pytest.raises(IndexNotReadyError) as caught:
        service.query(spec, limit=1)
    assert caught.value.status == "NOT_BUILT"

    metadata = service.build(spec)
    assert metadata["status"] == "READY"
    assert metadata["record_count"] == 3
    first, cursor = service.query(spec, limit=1, sample_id="sample-a")
    assert first == [rows[0]]
    assert cursor is not None
    second, cursor = service.query(spec, limit=1, sample_id="sample-a", cursor=cursor)
    assert second == [rows[1]]
    assert cursor is None
    assert service.get(spec, "row-2") == rows[2]

    encoded = json.dumps(rows[0]).encode()
    assert store.read_range(stored.uri, 0, len(encoded)) == encoded


def test_failed_checksum_build_does_not_change_source_artifact(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    stored = _source(store, [{"row_id": "row-1"}])
    original = store.read(stored.uri)
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{'0' * 64}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    with pytest.raises(ValueError, match="checksum mismatch"):
        service.build(spec)
    assert service.status(spec)["status"] == "FAILED"
    assert store.read(stored.uri) == original
    assert hashlib.sha256(original).hexdigest() == stored.sha256


def test_s3_range_read_uses_http_range_header():
    calls = []

    class Client:
        def get_object(self, **kwargs):
            calls.append(kwargs)
            return {"Body": io.BytesIO(b"target")}

    store = object.__new__(S3ArtifactStore)
    store.bucket = "bucket"
    store.prefix = "prefix"
    store._client = Client()
    assert store.read_range("artifact.jsonl", 10, 6) == b"target"
    assert calls == [
        {"Bucket": "bucket", "Key": "prefix/artifact.jsonl", "Range": "bytes=10-15"}
    ]


def test_missing_index_is_stale_and_force_rebuild_recovers(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    stored = _source(store, [{"row_id": "row-1", "sample_id": "sample-1"}])
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{stored.sha256}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    built = service.build(spec)
    store.delete(built["index_uri"])

    assert service.status(spec)["status"] == "STALE"
    with pytest.raises(IndexNotReadyError) as caught:
        service.query(spec, limit=1)
    assert caught.value.status == "STALE"
    assert service.build(spec, force=True)["status"] == "READY"
    assert service.get(spec, "row-1")["sample_id"] == "sample-1"


def test_concurrent_build_is_deduplicated(tmp_path, monkeypatch):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    stored = _source(store, [{"row_id": "row-1"}])
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{stored.sha256}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    entered, release = threading.Event(), threading.Event()
    original = service._build_locked

    def delayed_build(index_spec):
        entered.set()
        assert release.wait(timeout=5)
        return original(index_spec)

    monkeypatch.setattr(service, "_build_locked", delayed_build)
    completed = []
    worker = threading.Thread(target=lambda: completed.append(service.build(spec)))
    worker.start()
    assert entered.wait(timeout=5)
    duplicate = service.build(spec)
    release.set()
    worker.join(timeout=5)

    assert duplicate["status"] == "BUILDING"
    assert completed[0]["status"] == "READY"
    assert service.status(spec)["record_count"] == 1


def test_invalid_cursor_is_rejected_without_scanning_source(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    stored = _source(store, [{"row_id": "row-1"}])
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{stored.sha256}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    service.build(spec)
    with pytest.raises(ValueError, match="invalid index cursor"):
        service.query(spec, limit=1, cursor="%%%")


def test_raw_index_validates_manifest_and_source_bytes(tmp_path):
    from dataclasses import replace
    from uuid import uuid4

    from qa_eval.application.normalization.record_reader import RawRecordManifest

    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    source = _source(store, [{"raw": "original"}])
    manifest = RawRecordManifest(
        dataset_version_id=uuid4(),
        source_uri=source.uri,
        source_sha256="sha256:" + source.sha256,
        schema_fingerprint="sha256:" + "0" * 64,
    )
    spec = ArtifactIndexSpec(
        source.uri, manifest.source_sha256, "sample_id", "SAMPLE", manifest
    )
    with pytest.raises(ValueError, match="does not match Manifest"):
        replace(spec, source_checksum="sha256:" + "1" * 64)
    indexes = ArtifactRecordIndexService(store)
    indexes.build(spec)
    assert indexes.query(spec, limit=1)[0][0].data == {"raw": "original"}
    source.path.write_bytes(source.path.read_bytes().replace(b"original", b"modified"))
    with pytest.raises(ValueError, match="indexed record checksum mismatch"):
        indexes.query(spec, limit=1)

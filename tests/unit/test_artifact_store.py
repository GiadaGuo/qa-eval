# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description: ArtifactStore 端口与本地文件系统实现测试。
# ====================================================

import hashlib
from pathlib import Path

import pytest

from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_local_store_commit_and_read(tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path)
    relative = "datasets/test/file.jsonl"
    staging = store.prepare_write(relative)
    content = b'{"sample_id":"a","text":"hello"}\n'
    staging.write_bytes(content)

    artifact = store.commit(relative, sha256=_sha256(content))

    assert artifact.uri == relative
    assert artifact.size_bytes == len(content)
    assert artifact.sha256 == _sha256(content)
    assert store.read(relative) == content
    assert store.exists(relative)


def test_local_store_rejects_checksum_mismatch(tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path)
    relative = "datasets/test/file.jsonl"
    staging = store.prepare_write(relative)
    staging.write_bytes(b"content")

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.commit(relative, sha256="0" * 64)


def test_local_store_rejects_path_traversal(tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="path traversal"):
        store.prepare_write("../escape.jsonl")

    with pytest.raises(ValueError):
        store.prepare_write("/absolute.jsonl")

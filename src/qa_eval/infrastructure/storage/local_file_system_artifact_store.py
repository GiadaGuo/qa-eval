# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 本地文件系统 ArtifactStore 实现，用于闭环 2 本地开发与快速验证。
# 产物按 dataset_id/dataset_version_id/... 分层存储，URI 使用相对路径。
# ====================================================

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from qa_eval.domain.ports.artifact_store import StoredArtifact


class LocalFileSystemArtifactStore:
    """将 artifact 持久化到本地文件系统的 ArtifactStore 实现。"""

    def __init__(self, root_dir: Path | str) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._staging_dir = self.root_dir / ".staging"
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def prepare_write(self, relative_path: str) -> Path:
        staging_path = self._staging_dir / _safe_relative_path(relative_path)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        return staging_path

    def commit(
        self,
        relative_path: str,
        *,
        sha256: str | None = None,
    ) -> StoredArtifact:
        safe_relative = _safe_relative_path(relative_path)
        staging_path = self._staging_dir / safe_relative
        if not staging_path.exists():
            raise FileNotFoundError(
                f"staging artifact not found: {staging_path}"
            )

        actual_sha256 = _sha256_file(staging_path)
        if sha256 is not None and actual_sha256 != sha256.lower():
            raise ValueError(
                f"checksum mismatch for {relative_path}: "
                f"expected {sha256}, got {actual_sha256}"
            )

        destination = self.root_dir / safe_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_path), str(destination))

        return StoredArtifact(
            uri=str(safe_relative),
            path=destination,
            size_bytes=destination.stat().st_size,
            sha256=actual_sha256,
        )

    def read(self, relative_path: str) -> bytes:
        destination = self.root_dir / _safe_relative_path(relative_path)
        if not destination.exists():
            raise FileNotFoundError(f"artifact not found: {relative_path}")
        return destination.read_bytes()

    def exists(self, relative_path: str) -> bool:
        return (self.root_dir / _safe_relative_path(relative_path)).exists()

    def delete(self, relative_path: str) -> None:
        destination = self.root_dir / _safe_relative_path(relative_path)
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

    def delete_prefix(self, prefix: str) -> None:
        safe_prefix = _safe_relative_path(prefix)
        target = self.root_dir / safe_prefix
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()


def _safe_relative_path(relative_path: str) -> Path:
    """拒绝路径穿越与绝对路径。"""
    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError("relative_path must be relative")
    # 禁止任何向上跳转的组件
    if ".." in path.parts:
        raise ValueError(f"path traversal is not allowed: {relative_path}")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

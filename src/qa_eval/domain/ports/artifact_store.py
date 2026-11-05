# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# ArtifactStore 领域端口：负责 raw/normalized/rejected/profile 等产物
# 的临时写入、checksum 校验和最终稳定 URI 暴露。
# ====================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredArtifact:
    """提交到 ArtifactStore 后的不可变引用。"""

    uri: str
    path: Path
    size_bytes: int
    sha256: str


class ArtifactStore(Protocol):
    """对象/文件存储抽象，支持本地文件系统和 S3/MinIO 兼容后端。"""

    def prepare_write(self, relative_path: str) -> Path:
        """返回一个可用于临时写入的本地路径。"""
        ...

    def commit(
        self,
        relative_path: str,
        *,
        sha256: str | None = None,
    ) -> StoredArtifact:
        """校验并提交临时文件，返回稳定的 artifact 引用。"""
        ...

    def put_file(self, relative_path: str, source_path: Path) -> StoredArtifact:
        """从本地文件原子/流式写入 artifact，避免共享 staging 路径竞争。"""
        ...

    def read(self, relative_path: str) -> bytes:
        """读取已提交 artifact 的内容。"""
        ...

    def materialize(self, relative_path: str, destination: Path) -> None:
        """将 artifact 流式复制到本地路径，避免大文件整体载入内存。"""
        ...

    def exists(self, relative_path: str) -> bool:
        """检查 artifact 是否已存在。"""
        ...

    def delete(self, relative_path: str) -> None:
        """删除已提交的 artifact；路径不存在时不报错。"""
        ...

    def delete_prefix(self, prefix: str) -> None:
        """删除指定前缀下的全部 artifact。"""
        ...

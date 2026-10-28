# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# DocumentVersion 应用服务：Markdown 说明文档的上传校验、存储、
# 列表、元数据、下载与删除（引用检查）。
# ====================================================

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from qa_eval.domain.entities.document_version import (
    DOCUMENT_FORMAT_MARKDOWN,
    DocumentVersion,
)
from qa_eval.domain.exceptions import DocumentVersionInUseError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.domain.repositories.dataset_repository import DatasetRepository
from qa_eval.domain.repositories.document_version_repository import (
    DocumentVersionRepository,
)
from qa_eval.infrastructure.uuid7 import uuid7

logger = logging.getLogger(__name__)


class DocumentVersionService:
    """协调说明文档版本的生命周期；上传后不可变，版本号自动分配。"""

    MAX_SIZE_BYTES = 2 * 1024 * 1024  # MVP 上限 2MB

    def __init__(
        self,
        dataset_repository: DatasetRepository,
        document_version_repository: DocumentVersionRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._dataset_repository = dataset_repository
        self._document_repository = document_version_repository
        self._artifact_store = artifact_store

    def upload_document(
        self,
        dataset_id: UUID,
        filename: str,
        content: bytes,
    ) -> DocumentVersion:
        """校验并上传 Markdown 说明文档，返回新建的文档版本。

        - 数据集不存在：LookupError（路由层映射 404）
        - 校验失败（扩展名 / 大小 / 编码 / 空文件）：ValueError（路由层映射 422）
        """
        if self._dataset_repository.get_by_id(dataset_id) is None:
            raise LookupError(f"Dataset not found: {dataset_id}")

        filename = self._validate_document(filename, content)

        document_id = uuid7()
        version_label = self._document_repository.next_version_label(dataset_id)
        relative_path = f"datasets/{dataset_id}/documents/{document_id}/{filename}"
        staging = self._artifact_store.prepare_write(relative_path)
        staging.write_bytes(content)
        stored = self._artifact_store.commit(relative_path)

        version = DocumentVersion(
            id=document_id,
            dataset_id=dataset_id,
            version=version_label,
            filename=filename,
            format=DOCUMENT_FORMAT_MARKDOWN,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_uri=stored.uri,
        )
        try:
            return self._document_repository.add(version)
        except IntegrityError as exc:
            # 并发上传窗口内版本号分配冲突，由唯一约束兜底
            logger.warning(
                "Concurrent upload detected for dataset %s: %s",
                dataset_id,
                exc,
            )
            raise ValueError("版本号分配冲突，请重试上传") from exc

    def list_documents(self, dataset_id: UUID) -> list[DocumentVersion]:
        return self._document_repository.list_by_dataset(dataset_id)

    def get_document(self, document_version_id: UUID) -> DocumentVersion | None:
        return self._document_repository.get_by_id(document_version_id)

    def get_document_metadata(self, document_version_id: UUID) -> dict | None:
        """文档元数据：sha256、行数（按需从 ArtifactStore 读取）、解析状态。"""

        version = self._document_repository.get_by_id(document_version_id)
        if version is None:
            return None

        line_count: int | None
        try:
            text = self._artifact_store.read(version.storage_uri).decode("utf-8")
            line_count = len(text.splitlines())
        except FileNotFoundError:
            line_count = None

        return {
            "id": version.id,
            "dataset_id": version.dataset_id,
            "version": version.version,
            "filename": version.filename,
            "format": version.format,
            "size_bytes": version.size_bytes,
            "sha256": version.sha256,
            "line_count": line_count,
            "parse_status": ("PARSED" if version.parsed_artifact_uri else "NOT_PARSED"),
            "created_at": version.created_at.isoformat()
            if version.created_at
            else None,
        }

    def download_document(
        self, document_version_id: UUID
    ) -> tuple[DocumentVersion, bytes]:
        """读取原始文件内容；DB 行存在但 artifact 缺失时抛 FileNotFoundError。"""

        version = self._document_repository.get_by_id(document_version_id)
        if version is None:
            raise ValueError(f"DocumentVersion not found: {document_version_id}")
        return version, self._artifact_store.read(version.storage_uri)

    def delete_document(self, document_version_id: UUID) -> bool:
        """删除文档版本。

        - 不存在：返回 False（路由层映射 404）
        - 存在报告任务引用：抛 DocumentVersionInUseError（路由层映射 409）
        """

        version = self._document_repository.get_by_id(document_version_id)

        if version is None:
            return False

        dependencies = self._document_repository.count_blocking_dependencies(
            document_version_id
        )
        if any(dependencies.values()):
            raise DocumentVersionInUseError(dependencies)

        try:
            self._document_repository.delete(document_version_id)
        except IntegrityError as exc:
            # 依赖检查与删除之间存在并发新增引用的窗口，由 RESTRICT 兜底
            logger.warning(
                "Concurrent reference detected while deleting document %s: %s",
                document_version_id,
                exc,
            )
            raise DocumentVersionInUseError(
                {}, message="文档版本删除时检测到并发引用，请重试"
            ) from exc

        return True

    def _validate_document(self, filename: str, content: bytes) -> str:
        """上传校验：.md 扩展名、≤2MB、UTF-8 可解码、非空；返回安全文件名。"""

        if not filename or not filename.strip().endswith(".md"):
            raise ValueError("仅支持 Markdown（.md）文件；PDF/HTML/DOCX 等暂不支持")
        # 去掉目录成分，防止写入路径分叉
        safe_name = Path(filename.strip()).name
        if not safe_name.endswith(".md"):
            raise ValueError("文件名不合法")

        if len(content) > self.MAX_SIZE_BYTES:
            raise ValueError(
                f"文件大小超过上限（{self.MAX_SIZE_BYTES // (1024 * 1024)}MB）"
            )

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("说明文档必须为 UTF-8 编码") from exc

        if not text.strip():
            raise ValueError("说明文档内容为空")

        return safe_name

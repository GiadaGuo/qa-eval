# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档版本（DocumentVersion）领域实体：归属 Dataset，
# 上传后不可变，无 DRAFT/PUBLISHED 生命周期。
# ====================================================

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

DOCUMENT_FORMAT_MARKDOWN = "markdown"


@dataclass
class DocumentVersion:
    dataset_id: UUID
    version: str  # 上传时自动分配 v1、v2…，不可自定义（MVP）
    filename: str
    format: str  # 恒为 "markdown"（MVP）
    size_bytes: int
    sha256: str  # 原始文件哈希，报告可复现性的锚点
    storage_uri: str  # ArtifactStore URI（原始文件）
    id: UUID = field(default_factory=uuid4)
    parsed_artifact_uri: str | None = None  # 行号化解析产物，首次生成任务时产出
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Document version cannot be empty")
        if not self.filename.strip():
            raise ValueError("Document filename cannot be empty")
        if self.format != DOCUMENT_FORMAT_MARKDOWN:
            raise ValueError(f"Unsupported document format: {self.format}")
        if self.size_bytes <= 0:
            raise ValueError("Document size must be positive")
        if len(self.sha256) != 64:
            raise ValueError("Document sha256 must be 64 hex characters")
        if not self.storage_uri:
            raise ValueError("Document storage_uri cannot be empty")

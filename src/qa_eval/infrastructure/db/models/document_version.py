# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档版本与文档评测任务 ORM 模型（列 + document JSON 模式）。
# ====================================================

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base


class DocumentVersionModel(Base):
    __tablename__ = "document_versions"

    __table_args__ = (
        # 同一数据集下版本号唯一（v1、v2…由平台自动分配）
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_document_versions_dataset_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    storage_uri: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    # filename / format / parsed_artifact_uri 等细节保存在 document JSON
    document: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DocumentReportTaskModel(Base):
    """文档评测任务：P1 仅建表供引用检查，执行器在 P2 落地。"""

    __tablename__ = "document_report_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    evaluator_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    standard_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    standard_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    # 模型、prompt 版本、参数等冻结配置快照
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    # 阶段进度、逐指标耗时与调用统计、error
    document: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

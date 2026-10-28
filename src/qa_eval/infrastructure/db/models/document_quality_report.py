# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档质量报告 ORM 模型：完整报告契约存 document JSONB，
# 列只保留索引与外键（列 + document JSON 模式）。
# ====================================================

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base


class DocumentQualityReportModel(Base):
    __tablename__ = "document_quality_reports"

    report_id: Mapped[uuid.UUID] = mapped_column(
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
    # 一项任务至多产出一份报告
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_report_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    execution_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    document: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

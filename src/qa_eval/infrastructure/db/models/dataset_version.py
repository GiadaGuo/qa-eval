# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 17:06:02
# @LastEditTime: 2026-10-19 17:06:22
# @Description: 
# ====================================================

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"

    __table_args__ = (
        # 保证同一个逻辑数据集下不能出现两个 v1
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_version",
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
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="DRAFT",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    artifact_refs: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    content_fingerprint: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

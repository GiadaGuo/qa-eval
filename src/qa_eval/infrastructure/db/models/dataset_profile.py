# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetProfile ORM 模型，保存数据集画像的 canonical document。
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
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class DatasetProfileModel(Base):
    __tablename__ = "dataset_profiles"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "profiler_key",
            "profiler_version",
            "config_fingerprint",
            name="uq_dataset_profile_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "dataset_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    document: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    checksum_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    profiler_key: Mapped[str] = mapped_column(String(128), nullable=False)
    profiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

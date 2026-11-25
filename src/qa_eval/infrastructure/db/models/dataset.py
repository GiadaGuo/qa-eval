# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 14:20:21
# @Description: 
# ====================================================

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, text, true
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    modality: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    data_type: Mapped[str | None] = mapped_column(String(32))
    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    source_kind: Mapped[str | None] = mapped_column(String(32))
    storage_path: Mapped[str | None] = mapped_column(Text)
    source_config: Mapped[dict | None] = mapped_column(JSONB)
    custom_semantics: Mapped[list[dict] | None] = mapped_column(JSONB)
    version_sequence: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

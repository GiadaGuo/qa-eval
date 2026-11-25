from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB


class MappingRevisionModel(Base):
    __tablename__ = "mapping_revisions"

    dataset_version_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    revision_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

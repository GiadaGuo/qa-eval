import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base


class QualityContractVersionModel(Base):
    __tablename__ = "quality_contract_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "version_label"),
        UniqueConstraint("fingerprint"),
    )

    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    target_dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    version_label: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str] = mapped_column(String(80))
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

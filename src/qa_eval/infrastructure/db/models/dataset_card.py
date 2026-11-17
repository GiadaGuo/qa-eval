from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class DatasetCardModel(Base):
    __tablename__ = "dataset_cards"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "revision", name="uq_dataset_cards_version_revision"
        ),
        Index(
            "ix_dataset_cards_version_status",
            "dataset_version_id",
            "lifecycle_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DatasetProfileFieldCandidateModel(Base):
    __tablename__ = "dataset_profile_field_candidates"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "field_path", "value_hash", "source_type", "source_ref_hash",
            name="uq_profile_field_candidate_idempotency",
        ),
        Index(
            "ix_profile_field_candidates_lookup",
            "dataset_version_id",
            "field_path",
            "review_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    card_revision: Mapped[int | None] = mapped_column(Integer)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    value_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    value_role: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_ref_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_locator_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    extraction_rule_version: Mapped[str | None] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_profile_field_candidates.id", ondelete="RESTRICT")
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(256))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

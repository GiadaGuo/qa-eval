"""add dataset cards and profile field candidates

Revision ID: 8d4f1a2b3c4d
Revises: f6c3b47a12de
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from qa_eval.infrastructure.db.types import json_document_type

revision: str = "8d4f1a2b3c4d"
down_revision: str | None = "f6c3b47a12de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("lifecycle_status", sa.String(32), nullable=False),
        sa.Column("document", json_document_type(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_version_id",
            "revision",
            name="uq_dataset_cards_version_revision",
        ),
    )
    op.create_index(
        "ix_dataset_cards_dataset_version_id",
        "dataset_cards",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_dataset_cards_version_status",
        "dataset_cards",
        ["dataset_version_id", "lifecycle_status"],
    )

    op.create_table(
        "dataset_profile_field_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("card_revision", sa.Integer()),
        sa.Column("field_path", sa.String(512), nullable=False),
        sa.Column("value_json", json_document_type(), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("value_role", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_ref_json", json_document_type(), nullable=False),
        sa.Column("source_ref_hash", sa.String(64), nullable=False),
        sa.Column("evidence_locator_json", json_document_type(), nullable=False),
        sa.Column("extraction_rule_version", sa.String(64)),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid()),
        sa.Column("reviewed_by", sa.String(256)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["dataset_profile_field_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_version_id",
            "field_path",
            "value_hash",
            "source_type",
            "source_ref_hash",
            name="uq_profile_field_candidate_idempotency",
        ),
    )
    op.create_index(
        "ix_dataset_profile_field_candidates_dataset_version_id",
        "dataset_profile_field_candidates",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_profile_field_candidates_lookup",
        "dataset_profile_field_candidates",
        ["dataset_version_id", "field_path", "review_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_profile_field_candidates_lookup",
        table_name="dataset_profile_field_candidates",
    )
    op.drop_index(
        "ix_dataset_profile_field_candidates_dataset_version_id",
        table_name="dataset_profile_field_candidates",
    )
    op.drop_table("dataset_profile_field_candidates")
    op.drop_index("ix_dataset_cards_version_status", table_name="dataset_cards")
    op.drop_index("ix_dataset_cards_dataset_version_id", table_name="dataset_cards")
    op.drop_table("dataset_cards")

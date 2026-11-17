"""add immutable profile snapshots

Revision ID: 9e5a2b3c4d6e
Revises: 8d4f1a2b3c4d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from qa_eval.infrastructure.db.types import json_document_type

revision: str = "9e5a2b3c4d6e"
down_revision: str | None = "8d4f1a2b3c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("card_revision_id", sa.Uuid()),
        sa.Column("observed_profile_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_uri", sa.String(1024), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("rule_versions", json_document_type(), nullable=False),
        sa.Column("document", json_document_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["card_revision_id"], ["dataset_cards.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["observed_profile_id"], ["dataset_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_version_id",
            "content_sha256",
            name="uq_profile_snapshots_version_content",
        ),
    )
    op.create_index(
        "ix_profile_snapshots_dataset_version_id",
        "profile_snapshots",
        ["dataset_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_profile_snapshots_dataset_version_id", table_name="profile_snapshots"
    )
    op.drop_table("profile_snapshots")

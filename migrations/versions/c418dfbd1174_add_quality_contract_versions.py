"""add quality contract versions

Revision ID: c418dfbd1174
Revises: b47a21d60f52
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c418dfbd1174"
down_revision: str | Sequence[str] | None = "b47a21d60f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_contract_versions",
        sa.Column("contract_version_id", sa.Uuid(), primary_key=True),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("target_dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(80), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("contract_id", "version_label"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index("ix_quality_contract_versions_contract_id", "quality_contract_versions", ["contract_id"])
    op.create_index("ix_quality_contract_versions_target_dataset_version_id", "quality_contract_versions", ["target_dataset_version_id"])
    op.create_index("ix_quality_contract_versions_status", "quality_contract_versions", ["status"])


def downgrade() -> None:
    op.drop_table("quality_contract_versions")

"""add metric registry

Revision ID: 9c71f0e8a321
Revises: 3759c5e66bdb
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c71f0e8a321"
down_revision: str | Sequence[str] | None = "3759c5e66bdb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("metric_key", "version"),
    )
    op.create_index(
        "ix_metric_definitions_metric_key", "metric_definitions", ["metric_key"]
    )
    op.create_table(
        "calculator_implementations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("calculator_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("calculator_key", "version"),
    )
    op.create_index(
        "ix_calculator_implementations_calculator_key",
        "calculator_implementations",
        ["calculator_key"],
    )
    op.create_table(
        "metric_calculator_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("metric_version", sa.String(64), nullable=False),
        sa.Column("calculator_key", sa.String(128), nullable=False),
        sa.Column("calculator_version", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "metric_key", "metric_version", "calculator_key", "calculator_version"
        ),
    )
    op.create_index(
        "ix_metric_calculator_bindings_metric_key",
        "metric_calculator_bindings",
        ["metric_key"],
    )


def downgrade() -> None:
    op.drop_table("metric_calculator_bindings")
    op.drop_table("calculator_implementations")
    op.drop_table("metric_definitions")

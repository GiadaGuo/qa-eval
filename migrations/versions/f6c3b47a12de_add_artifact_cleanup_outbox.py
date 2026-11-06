"""add artifact cleanup outbox

Revision ID: f6c3b47a12de
Revises: c9f4a21d8e73
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c3b47a12de"
down_revision: str | None = "c9f4a21d8e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_cleanup_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target", sa.String(1024), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target"),
    )
    op.create_index("ix_artifact_cleanup_tasks_status", "artifact_cleanup_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_artifact_cleanup_tasks_status", table_name="artifact_cleanup_tasks")
    op.drop_table("artifact_cleanup_tasks")

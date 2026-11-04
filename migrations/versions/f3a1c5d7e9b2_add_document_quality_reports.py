"""add document quality reports

Revision ID: f3a1c5d7e9b2
Revises: b4e7f2a8c1d9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1c5d7e9b2"
down_revision: str | Sequence[str] | None = "b4e7f2a8c1d9"
branch_labels: None = None
depends_on: None


def upgrade() -> None:
    op.create_table(
        "document_quality_reports",
        sa.Column("report_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey(
                "document_versions.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey(
                "document_report_tasks.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("execution_status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "task_id", name="uq_document_quality_reports_task_id"
        ),
    )
    op.create_index(
        "ix_document_quality_reports_document_version_id",
        "document_quality_reports",
        ["document_version_id"],
    )
    op.create_index(
        "ix_document_quality_reports_execution_status",
        "document_quality_reports",
        ["execution_status"],
    )
def downgrade() -> None:
    op.drop_table("document_quality_reports")

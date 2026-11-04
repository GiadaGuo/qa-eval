"""add immutable scored quality report snapshots

Revision ID: c9f4a21d8e73
Revises: e84d9b73a6f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f4a21d8e73"
down_revision: str | Sequence[str] | None = "e84d9b73a6f1"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "scored_quality_reports",
        sa.Column("scored_report_id", sa.Uuid(), primary_key=True),
        sa.Column("base_report_id", sa.Uuid(), sa.ForeignKey("quality_reports.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("scoring_profile_id", sa.String(128), nullable=False),
        sa.Column("scoring_profile_version", sa.String(32), nullable=False),
        sa.Column("report_schema_version", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "run_id", "scoring_profile_id", "scoring_profile_version", "report_schema_version",
            name="uq_scored_quality_report_identity",
        ),
    )
    op.create_index("ix_scored_quality_reports_base_report_id", "scored_quality_reports", ["base_report_id"])
    op.create_index("ix_scored_quality_reports_run_id", "scored_quality_reports", ["run_id"])
    op.create_index("ix_scored_quality_reports_scoring_profile_id", "scored_quality_reports", ["scoring_profile_id"])


def downgrade() -> None:
    op.drop_table("scored_quality_reports")

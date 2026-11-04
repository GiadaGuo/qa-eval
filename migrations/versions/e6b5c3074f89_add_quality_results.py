"""add quality results

Revision ID: e6b5c3074f89
Revises: d91a837db256
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6b5c3074f89"
down_revision: str | Sequence[str] | None = "d91a837db256"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_evaluations",
        sa.Column("rule_evaluation_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_result_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["metric_result_id"], ["metric_results.metric_result_id"], ondelete="RESTRICT"),
    )
    for column in ("run_id", "metric_result_id", "status"):
        op.create_index(f"ix_rule_evaluations_{column}", "rule_evaluations", [column])
    op.create_table(
        "quality_issues",
        sa.Column("issue_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_result_id", sa.Uuid(), nullable=False),
        sa.Column("determination", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("dedup_key", sa.String(512), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["metric_result_id"], ["metric_results.metric_result_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "dedup_key"),
    )
    for column in ("run_id", "metric_result_id", "determination", "severity", "code"):
        op.create_index(f"ix_quality_issues_{column}", "quality_issues", [column])
    op.create_table(
        "issue_indices",
        sa.Column("issue_index_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_result_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["metric_result_id"], ["metric_results.metric_result_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_issue_indices_run_id", "issue_indices", ["run_id"])
    op.create_table(
        "quality_reports",
        sa.Column("report_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("execution_status", sa.String(32), nullable=False),
        sa.Column("gate_status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contract_version_id"], ["quality_contract_versions.contract_version_id"], ondelete="RESTRICT"),
    )
    for column in ("run_id", "dataset_version_id", "contract_version_id", "execution_status", "gate_status"):
        op.create_index(
            f"ix_quality_reports_{column}",
            "quality_reports",
            [column],
            unique=column == "run_id",
        )


def downgrade() -> None:
    op.drop_table("quality_reports")
    op.drop_table("issue_indices")
    op.drop_table("quality_issues")
    op.drop_table("rule_evaluations")

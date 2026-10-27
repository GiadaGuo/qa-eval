"""add evaluation runs

Revision ID: d91a837db256
Revises: c418dfbd1174
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d91a837db256"
down_revision: str | Sequence[str] | None = "c418dfbd1174"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physical_evaluation_plans",
        sa.Column("physical_plan_id", sa.UUID(), primary_key=True),
        sa.Column("dataset_version_id", sa.UUID(), nullable=False),
        sa.Column("contract_version_id", sa.UUID(), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(80), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contract_version_id"], ["quality_contract_versions.contract_version_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_physical_evaluation_plans_dataset_version_id", "physical_evaluation_plans", ["dataset_version_id"])
    op.create_index("ix_physical_evaluation_plans_contract_version_id", "physical_evaluation_plans", ["contract_version_id"])
    op.create_index("ix_physical_evaluation_plans_semantic_fingerprint", "physical_evaluation_plans", ["semantic_fingerprint"])
    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.UUID(), primary_key=True),
        sa.Column("physical_plan_id", sa.UUID(), nullable=False),
        sa.Column("dataset_version_id", sa.UUID(), nullable=False),
        sa.Column("contract_version_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["physical_plan_id"], ["physical_evaluation_plans.physical_plan_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contract_version_id"], ["quality_contract_versions.contract_version_id"], ondelete="RESTRICT"),
    )
    for column in ("physical_plan_id", "dataset_version_id", "contract_version_id", "status"):
        op.create_index(f"ix_evaluation_runs_{column}", "evaluation_runs", [column])
    op.create_table(
        "stage_runs",
        sa.Column("stage_run_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("physical_plan_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.String(255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["physical_plan_id"], ["physical_evaluation_plans.physical_plan_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "stage_id", "attempt"),
    )
    op.create_index("ix_stage_runs_run_id", "stage_runs", ["run_id"])
    op.create_index("ix_stage_runs_status", "stage_runs", ["status"])
    op.create_table(
        "metric_results",
        sa.Column("metric_result_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("stage_run_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("dataset_version_id", sa.UUID(), nullable=False),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("metric_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_run_id"], ["stage_runs.stage_run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"),
    )
    for column in ("run_id", "dataset_version_id", "metric_key", "status"):
        op.create_index(f"ix_metric_results_{column}", "metric_results", [column])


def downgrade() -> None:
    op.drop_table("metric_results")
    op.drop_table("stage_runs")
    op.drop_table("evaluation_runs")
    op.drop_table("physical_evaluation_plans")

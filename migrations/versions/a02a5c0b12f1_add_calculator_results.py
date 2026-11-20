"""Add independently addressable native Calculator results."""

import sqlalchemy as sa
from alembic import op

from qa_eval.infrastructure.db.types import json_document_type

revision = "a02a5c0b12f1"
down_revision = "9e5a2b3c4d6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calculator_results",
        sa.Column("calculator_result_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stage_run_id",
            sa.Uuid(),
            sa.ForeignKey("stage_runs.stage_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_item_key", sa.String(255), nullable=False),
        sa.Column("output_slot_id", sa.String(255), nullable=False),
        sa.Column("document", json_document_type(), nullable=False),
        sa.UniqueConstraint("stage_run_id", "output_slot_id"),
    )
    op.create_index("ix_calculator_results_run_id", "calculator_results", ["run_id"])
    op.create_table(
        "calculator_result_artifacts",
        sa.Column("evidence_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "calculator_result_id",
            sa.Uuid(),
            sa.ForeignKey(
                "calculator_results.calculator_result_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_calculator_result_artifacts_calculator_result_id",
        "calculator_result_artifacts",
        ["calculator_result_id"],
    )


def downgrade():
    op.drop_table("calculator_result_artifacts")
    op.drop_table("calculator_results")

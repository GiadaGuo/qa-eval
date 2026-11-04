"""allow multiple metric results per shared stage

Revision ID: e84d9b73a6f1
Revises: c2f7a91d4b10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e84d9b73a6f1"
down_revision: str | Sequence[str] | None = "c2f7a91d4b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("metric_results") as batch_op:
        batch_op.drop_constraint(
            "metric_results_stage_run_id_key", type_="unique"
        )
        batch_op.create_index(
            "ix_metric_results_stage_run_id", ["stage_run_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_results") as batch_op:
        batch_op.drop_index("ix_metric_results_stage_run_id")
        batch_op.create_unique_constraint(
            "metric_results_stage_run_id_key", ["stage_run_id"]
        )

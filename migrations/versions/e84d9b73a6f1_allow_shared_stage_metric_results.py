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
    op.drop_constraint(
        "metric_results_stage_run_id_key", "metric_results", type_="unique"
    )
    op.create_index(
        "ix_metric_results_stage_run_id", "metric_results", ["stage_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_metric_results_stage_run_id", table_name="metric_results")
    op.create_unique_constraint(
        "metric_results_stage_run_id_key", "metric_results", ["stage_run_id"]
    )

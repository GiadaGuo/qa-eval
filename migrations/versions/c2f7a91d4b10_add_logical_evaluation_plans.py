"""add logical evaluation plans and physical plan lineage

Revision ID: c2f7a91d4b10
Revises: a7d2c6f41b90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2f7a91d4b10"
down_revision: str | Sequence[str] | None = "a7d2c6f41b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "logical_evaluation_plans",
        sa.Column("logical_plan_id", sa.UUID(), primary_key=True),
        sa.Column("dataset_version_id", sa.UUID(), nullable=False),
        sa.Column("contract_version_id", sa.UUID(), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(80), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id"],
            ["quality_contract_versions.contract_version_id"],
            ondelete="RESTRICT",
        ),
    )
    for column in ("dataset_version_id", "contract_version_id", "semantic_fingerprint"):
        op.create_index(
            f"ix_logical_evaluation_plans_{column}",
            "logical_evaluation_plans",
            [column],
        )
    op.add_column(
        "physical_evaluation_plans",
        sa.Column("logical_plan_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_physical_plan_logical_plan",
        "physical_evaluation_plans",
        "logical_evaluation_plans",
        ["logical_plan_id"],
        ["logical_plan_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_physical_evaluation_plans_logical_plan_id",
        "physical_evaluation_plans",
        ["logical_plan_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_physical_evaluation_plans_logical_plan_id",
        table_name="physical_evaluation_plans",
    )
    op.drop_constraint(
        "fk_physical_plan_logical_plan", "physical_evaluation_plans", type_="foreignkey"
    )
    op.drop_column("physical_evaluation_plans", "logical_plan_id")
    op.drop_table("logical_evaluation_plans")

"""Add CalculatorResult sources for native rules and quality issues."""

import sqlalchemy as sa
from alembic import op

revision = "c02c5c0b12f2"
down_revision = "a02a5c0b12f1"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("rule_evaluations", "quality_issues"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "metric_result_id", existing_type=sa.Uuid(), nullable=True
            )
            batch.add_column(sa.Column("calculator_result_id", sa.Uuid()))
            batch.create_foreign_key(
                f"fk_{table}_calculator_result",
                "calculator_results",
                ["calculator_result_id"],
                ["calculator_result_id"],
                ondelete="RESTRICT",
            )
            batch.create_index(
                f"ix_{table}_calculator_result_id", ["calculator_result_id"]
            )
            condition = (
                "(metric_result_id IS NULL) <> (calculator_result_id IS NULL)"
                if table == "quality_issues"
                else "metric_result_id IS NULL OR calculator_result_id IS NULL"
            )
            batch.create_check_constraint(f"ck_{table}_result_source", condition)


def downgrade():
    connection = op.get_bind()
    for table in ("rule_evaluations", "quality_issues"):
        if connection.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE metric_result_id IS NULL LIMIT 1")
        ).first():
            raise RuntimeError(
                "Phase 2C downgrade requires preserving native evaluation data first"
            )
    for table in ("rule_evaluations", "quality_issues"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_calculator_result_id")
            batch.drop_constraint(f"ck_{table}_result_source", type_="check")
            batch.drop_constraint(f"fk_{table}_calculator_result", type_="foreignkey")
            batch.drop_column("calculator_result_id")
            batch.alter_column(
                "metric_result_id", existing_type=sa.Uuid(), nullable=False
            )

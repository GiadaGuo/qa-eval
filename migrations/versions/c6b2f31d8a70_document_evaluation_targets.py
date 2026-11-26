"""Allow document versions as formal evaluation targets."""

import sqlalchemy as sa
from alembic import op

revision = "c6b2f31d8a70"
down_revision = "81de49ec44aa"

TARGETS = (
    ("quality_contract_versions", "target_dataset_version_id"),
    ("logical_evaluation_plans", "dataset_version_id"),
    ("physical_evaluation_plans", "dataset_version_id"),
    ("evaluation_runs", "dataset_version_id"),
    ("metric_results", "dataset_version_id"),
)


def upgrade():
    bind = op.get_bind()
    for table, _ in TARGETS:
        # SQLite DDL 非事务化，上次失败的 batch 重建可能残留临时表；
        # 仅在原表仍在时丢弃它（原表不在说明失败发生在改名前后，需人工处理）。
        if sa.inspect(bind).has_table(table):
            bind.execute(sa.text(f"DROP TABLE IF EXISTS _alembic_tmp_{table}"))
    for table, column in TARGETS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Uuid(), nullable=True)
            batch.add_column(sa.Column("document_version_id", sa.Uuid()))
            batch.create_foreign_key(
                f"fk_{table}_doc",
                "document_versions",
                ["document_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_index(
                f"ix_{table}_document_version_id", ["document_version_id"]
            )
            batch.create_check_constraint(
                f"ck_{table}_target",
                f"({column} IS NULL) <> (document_version_id IS NULL)",
            )


def downgrade():
    for table, _ in TARGETS:
        if (
            op.get_bind()
            .execute(
                sa.text(
                    f"SELECT 1 FROM {table} WHERE document_version_id IS NOT NULL LIMIT 1"
                )
            )
            .first()
        ):
            raise RuntimeError("archive formal document evaluations before downgrading")
    for table, column in reversed(TARGETS):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_target", type_="check")
            batch.drop_constraint(f"fk_{table}_doc", type_="foreignkey")
            batch.drop_index(f"ix_{table}_document_version_id")
            batch.drop_column("document_version_id")
            batch.alter_column(column, existing_type=sa.Uuid(), nullable=False)

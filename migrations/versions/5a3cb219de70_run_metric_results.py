"""Version metric results and allow run-level aggregation rows."""

from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "5a3cb219de70"
down_revision = "7d3f74b8e129"
branch_labels = None
depends_on = None


@contextmanager
def _alter_metric_results():
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        with op.batch_alter_table("metric_results") as batch:
            yield batch
        return
    # SQLite must rebuild a table referenced by issue/rule/report rows.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("BEGIN")
            with op.batch_alter_table("metric_results") as batch:
                yield batch
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise RuntimeError(
                    "foreign key check failed after rebuilding metric_results"
                )
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade():
    with _alter_metric_results() as batch:
        batch.add_column(
            sa.Column(
                "schema_version", sa.String(8), nullable=False, server_default="1.0"
            )
        )
        batch.alter_column("stage_run_id", nullable=True)
        batch.create_check_constraint(
            "ck_metric_result_version_stage",
            "(schema_version = '1.0' AND stage_run_id IS NOT NULL) OR "
            "(schema_version = '2.0' AND stage_run_id IS NULL)",
        )
    predicate = sa.text("schema_version = '2.0'")
    op.create_index(
        "uq_run_metric_result_v2",
        "metric_results",
        ["run_id", "metric_key", "metric_version"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def downgrade():
    table = sa.table("metric_results", sa.column("schema_version"))
    if (
        op.get_bind()
        .execute(
            sa.select(sa.literal(1)).where(table.c.schema_version == "2.0").limit(1)
        )
        .first()
    ):
        raise RuntimeError("archive version 2.0 metric results before downgrading")
    with _alter_metric_results() as batch:
        batch.drop_index("uq_run_metric_result_v2")
        batch.drop_constraint("ck_metric_result_version_stage", type_="check")
        batch.alter_column("stage_run_id", nullable=False)
        batch.drop_column("schema_version")

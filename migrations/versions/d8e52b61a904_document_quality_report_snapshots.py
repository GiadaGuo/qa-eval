"""Store independent document and combined V2 reports."""

import sqlalchemy as sa
from alembic import op

revision = "d8e52b61a904"
down_revision = "c6b2f31d8a70"


def upgrade():
    with op.batch_alter_table("quality_reports") as batch:
        batch.alter_column("dataset_version_id", existing_type=sa.Uuid(), nullable=True)
        for column, target, key in (
            ("document_version_id", "document_versions", "id"),
            ("source_report_id", "quality_reports", "report_id"),
            ("source_document_report_id", "quality_reports", "report_id"),
        ):
            batch.add_column(sa.Column(column, sa.Uuid()))
            batch.create_foreign_key(
                f"fk_quality_reports_{column}",
                target,
                [column],
                [key],
                ondelete="RESTRICT",
            )
        batch.create_check_constraint(
            "ck_quality_reports_target",
            "(dataset_version_id IS NULL) <> (document_version_id IS NULL)",
        )
        batch.create_check_constraint(
            "ck_quality_reports_sources",
            "(source_report_id IS NULL) = (source_document_report_id IS NULL)",
        )
        batch.create_unique_constraint(
            "uq_quality_reports_source_pair",
            ["source_report_id", "source_document_report_id"],
        )
        batch.drop_index("ix_quality_reports_run_id")
        batch.create_index("ix_quality_reports_run_id", ["run_id"])
        batch.create_index(
            "uq_quality_reports_base_run",
            ["run_id"],
            unique=True,
            sqlite_where=sa.text("source_report_id IS NULL"),
            postgresql_where=sa.text("source_report_id IS NULL"),
        )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT 1 FROM quality_reports WHERE document_version_id IS NOT NULL OR source_report_id IS NOT NULL LIMIT 1"
        )
    ):
        raise RuntimeError(
            "archive document and combined report snapshots before downgrading"
        )
    with op.batch_alter_table("quality_reports") as batch:
        batch.drop_index("uq_quality_reports_base_run")
        batch.drop_index("ix_quality_reports_run_id")
        batch.create_index("ix_quality_reports_run_id", ["run_id"], unique=True)
        batch.drop_constraint("uq_quality_reports_source_pair", type_="unique")
        batch.drop_constraint("ck_quality_reports_sources", type_="check")
        batch.drop_constraint("ck_quality_reports_target", type_="check")
        for column in (
            "source_document_report_id",
            "source_report_id",
            "document_version_id",
        ):
            batch.drop_constraint(f"fk_quality_reports_{column}", type_="foreignkey")
            batch.drop_column(column)
        batch.alter_column(
            "dataset_version_id", existing_type=sa.Uuid(), nullable=False
        )

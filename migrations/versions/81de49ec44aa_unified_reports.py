"""Add versioned unified reports and a frozen document report FK."""

import sqlalchemy as sa
from alembic import op

revision = "81de49ec44aa"
down_revision = "5a3cb219de70"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("quality_reports") as batch:
        batch.add_column(
            sa.Column(
                "schema_version", sa.String(8), nullable=False, server_default="1.0"
            )
        )
        batch.add_column(sa.Column("document_quality_report_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_quality_report_document_report",
            "document_quality_reports",
            ["document_quality_report_id"],
            ["report_id"],
            ondelete="RESTRICT",
        )
    reports = sa.table(
        "quality_reports", sa.column("schema_version"), sa.column("document")
    )
    bind = op.get_bind()
    expression = (
        "json_extract(document, '$.schema_version')"
        if bind.dialect.name == "sqlite"
        else "document->>'schema_version'"
    )
    op.execute(
        reports.update()
        .where(sa.text(f"{expression} = '1.1'"))
        .values(schema_version="1.1")
    )


def downgrade():
    reports = sa.table("quality_reports", sa.column("schema_version"))
    if (
        op.get_bind()
        .execute(
            sa.select(sa.literal(1)).where(reports.c.schema_version == "2.0").limit(1)
        )
        .first()
    ):
        raise RuntimeError("archive unified quality reports before downgrading")
    with op.batch_alter_table("quality_reports") as batch:
        batch.drop_constraint("fk_quality_report_document_report", type_="foreignkey")
        batch.drop_column("document_quality_report_id")
        batch.drop_column("schema_version")

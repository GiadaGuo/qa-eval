"""Add dataset management metadata and durable version numbering."""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "761aa08df154"
down_revision = "c02c5c0b12f2"


def upgrade():
    op.add_column("datasets", sa.Column("data_type", sa.String(32)))
    op.add_column("datasets", sa.Column("owner", sa.String(255)))
    op.add_column("datasets", sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("datasets", sa.Column("source_kind", sa.String(32)))
    op.add_column("datasets", sa.Column("storage_path", sa.Text()))
    op.add_column("datasets", sa.Column("source_config", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")))
    op.add_column("datasets", sa.Column("version_sequence", sa.Integer(), server_default="0", nullable=False))
    op.create_table(
        "dataset_source_rows",
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("row_number", sa.Integer(), primary_key=True),
        sa.Column("values", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    )
    connection = op.get_bind()
    maxima = {}
    for dataset_id, label in connection.execute(sa.text("SELECT dataset_id, version FROM dataset_versions")):
        if re.fullmatch(r"v[0-9]{1,9}", label):
            maxima[dataset_id] = max(maxima.get(dataset_id, 0), int(label[1:]))
    if maxima:
        connection.execute(
            sa.text("UPDATE datasets SET version_sequence = :number WHERE id = :dataset_id"),
            [{"dataset_id": key, "number": number} for key, number in maxima.items()],
        )


def downgrade():
    op.drop_table("dataset_source_rows")
    for name in ("version_sequence", "source_config", "storage_path", "source_kind", "enabled", "owner", "data_type"):
        op.drop_column("datasets", name)

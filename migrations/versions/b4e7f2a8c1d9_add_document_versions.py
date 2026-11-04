"""add document versions

Revision ID: b4e7f2a8c1d9
Revises: e6b5c3074f89
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e7f2a8c1d9"
down_revision: str | Sequence[str] | None = "e6b5c3074f89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("dataset_id", "version", name="uq_document_versions_dataset_version"),
    )
    op.create_index(
        "ix_document_versions_dataset_id",
        "document_versions",
        ["dataset_id"],
    )
    # P1 仅建表供文档版本删除引用检查；执行器与任务创建在 P2 落地
    op.create_table(
        "document_report_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("standard_version", sa.String(64), nullable=False),
        sa.Column("standard_sha256", sa.String(64), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_document_report_tasks_document_version_id",
        "document_report_tasks",
        ["document_version_id"],
    )
    op.create_index(
        "ix_document_report_tasks_status",
        "document_report_tasks",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("document_report_tasks")
    op.drop_table("document_versions")

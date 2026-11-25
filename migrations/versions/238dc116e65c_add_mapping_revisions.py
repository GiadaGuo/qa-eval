"""Add immutable confirmed semantic mapping revisions."""

import sqlalchemy as sa
from alembic import op

from qa_eval.infrastructure.db.types import json_document_type

revision = "238dc116e65c"
down_revision = "761aa08df154"


def upgrade() -> None:
    op.create_table(
        "mapping_revisions",
        sa.Column(
            "dataset_version_id", sa.Uuid(),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision_digest", sa.String(71), primary_key=True),
        sa.Column("document", json_document_type(), nullable=False),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("mapping_revisions")

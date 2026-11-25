"""Add dataset-scoped semantics and version-scoped mapping workflow state."""

import sqlalchemy as sa
from alembic import op

from qa_eval.infrastructure.db.types import json_document_type

revision = "7d3f74b8e129"
down_revision = "238dc116e65c"


def upgrade() -> None:
    op.add_column("datasets", sa.Column("custom_semantics", json_document_type()))
    op.add_column("dataset_versions", sa.Column("mapping_draft", json_document_type()))
    op.add_column("dataset_versions", sa.Column("default_mapping_digest", sa.String(71)))


def downgrade() -> None:
    op.drop_column("dataset_versions", "default_mapping_digest")
    op.drop_column("dataset_versions", "mapping_draft")
    op.drop_column("datasets", "custom_semantics")

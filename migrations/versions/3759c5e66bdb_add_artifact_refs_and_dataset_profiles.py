"""add artifact refs and dataset profiles

Revision ID: 3759c5e66bdb
Revises: 040cc5ac4d47
Create Date: 2026-10-27 14:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3759c5e66bdb'
down_revision: str | Sequence[str] | None = '040cc5ac4d47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'dataset_versions',
        sa.Column('artifact_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        'dataset_versions',
        sa.Column('content_fingerprint', sa.String(length=128), nullable=True)
    )
    op.create_index(
        op.f('ix_dataset_versions_content_fingerprint'),
        'dataset_versions',
        ['content_fingerprint'],
        unique=False,
    )

    op.create_table(
        'dataset_profiles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('dataset_version_id', sa.UUID(), nullable=False),
        sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['dataset_version_id'], ['dataset_versions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dataset_version_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('dataset_profiles')
    op.drop_index(op.f('ix_dataset_versions_content_fingerprint'), table_name='dataset_versions')
    op.drop_column('dataset_versions', 'content_fingerprint')
    op.drop_column('dataset_versions', 'artifact_refs')

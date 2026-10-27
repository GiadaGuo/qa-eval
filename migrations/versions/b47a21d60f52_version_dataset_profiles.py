"""version dataset profiles

Revision ID: b47a21d60f52
Revises: 9c71f0e8a321
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b47a21d60f52"
down_revision: str | Sequence[str] | None = "9c71f0e8a321"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "dataset_profiles_dataset_version_id_key", "dataset_profiles", type_="unique"
    )
    op.add_column(
        "dataset_profiles", sa.Column("profiler_key", sa.String(128), nullable=True)
    )
    op.add_column(
        "dataset_profiles", sa.Column("profiler_version", sa.String(64), nullable=True)
    )
    op.add_column(
        "dataset_profiles",
        sa.Column("config_fingerprint", sa.String(128), nullable=True),
    )
    op.execute(
        "UPDATE dataset_profiles SET profiler_key='base_profile', profiler_version='1.0.0', config_fingerprint='sha256:' || repeat('0', 64)"
    )
    op.alter_column("dataset_profiles", "profiler_key", nullable=False)
    op.alter_column("dataset_profiles", "profiler_version", nullable=False)
    op.alter_column("dataset_profiles", "config_fingerprint", nullable=False)
    op.create_index(
        "ix_dataset_profiles_dataset_version_id",
        "dataset_profiles",
        ["dataset_version_id"],
    )
    op.create_unique_constraint(
        "uq_dataset_profile_run",
        "dataset_profiles",
        [
            "dataset_version_id",
            "profiler_key",
            "profiler_version",
            "config_fingerprint",
        ],
    )


def downgrade() -> None:
    op.drop_constraint("uq_dataset_profile_run", "dataset_profiles", type_="unique")
    op.drop_index(
        "ix_dataset_profiles_dataset_version_id", table_name="dataset_profiles"
    )
    op.drop_column("dataset_profiles", "config_fingerprint")
    op.drop_column("dataset_profiles", "profiler_version")
    op.drop_column("dataset_profiles", "profiler_key")
    op.create_unique_constraint(
        "dataset_profiles_dataset_version_id_key",
        "dataset_profiles",
        ["dataset_version_id"],
    )

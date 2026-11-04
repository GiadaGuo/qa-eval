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
    with op.batch_alter_table("dataset_profiles") as batch_op:
        batch_op.drop_constraint(
            "dataset_profiles_dataset_version_id_key", type_="unique"
        )
        batch_op.add_column(
            sa.Column("profiler_key", sa.String(128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("profiler_version", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("config_fingerprint", sa.String(128), nullable=True)
        )

    op.execute(
        sa.text(
            "UPDATE dataset_profiles SET profiler_key=:profiler_key, "
            "profiler_version=:profiler_version, "
            "config_fingerprint=:config_fingerprint"
        ).bindparams(
            profiler_key="base_profile",
            profiler_version="1.0.0",
            config_fingerprint=f"sha256:{'0' * 64}",
        )
    )

    with op.batch_alter_table("dataset_profiles") as batch_op:
        batch_op.alter_column("profiler_key", nullable=False)
        batch_op.alter_column("profiler_version", nullable=False)
        batch_op.alter_column("config_fingerprint", nullable=False)
        batch_op.create_index(
            "ix_dataset_profiles_dataset_version_id",
            ["dataset_version_id"],
        )
        batch_op.create_unique_constraint(
            "uq_dataset_profile_run",
            [
                "dataset_version_id",
                "profiler_key",
                "profiler_version",
                "config_fingerprint",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("dataset_profiles") as batch_op:
        batch_op.drop_constraint("uq_dataset_profile_run", type_="unique")
        batch_op.drop_index("ix_dataset_profiles_dataset_version_id")
        batch_op.drop_column("config_fingerprint")
        batch_op.drop_column("profiler_version")
        batch_op.drop_column("profiler_key")
        batch_op.create_unique_constraint(
            "dataset_profiles_dataset_version_id_key",
            ["dataset_version_id"],
        )

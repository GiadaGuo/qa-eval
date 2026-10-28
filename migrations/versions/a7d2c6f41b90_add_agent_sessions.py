"""add agent session persistence

Revision ID: a7d2c6f41b90
Revises: f3a1c5d7e9b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d2c6f41b90"
down_revision: str | Sequence[str] | None = "f3a1c5d7e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("agent_sessions", sa.Column("session_id", sa.UUID(), primary_key=True), sa.Column("dataset_version_id", sa.UUID(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("active_skill", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"))
    op.create_index("ix_agent_sessions_dataset_version_id", "agent_sessions", ["dataset_version_id"])
    op.create_index("ix_agent_sessions_status", "agent_sessions", ["status"])
    op.create_table("conversation_messages", sa.Column("message_id", sa.UUID(), primary_key=True), sa.Column("session_id", sa.UUID(), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("content", sa.String(), nullable=False), sa.Column("attachments", postgresql.JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"))
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])
    op.create_table("skill_invocations", sa.Column("invocation_id", sa.UUID(), primary_key=True), sa.Column("session_id", sa.UUID(), nullable=False), sa.Column("skill_key", sa.String(255), nullable=False), sa.Column("skill_version", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("reason", sa.String()), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"))
    op.create_index("ix_skill_invocations_session_id", "skill_invocations", ["session_id"])
    op.create_index("ix_skill_invocations_status", "skill_invocations", ["status"])
    op.create_table("tool_invocations", sa.Column("invocation_id", sa.UUID(), primary_key=True), sa.Column("session_id", sa.UUID(), nullable=False), sa.Column("tool_name", sa.String(255), nullable=False), sa.Column("arguments_digest", sa.String(80), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("result_refs", postgresql.JSONB(), nullable=False), sa.Column("error", postgresql.JSONB()), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"))
    op.create_index("ix_tool_invocations_session_id", "tool_invocations", ["session_id"])
    op.create_index("ix_tool_invocations_status", "tool_invocations", ["status"])
    op.create_table("proposal_revisions", sa.Column("proposal_revision_id", sa.UUID(), primary_key=True), sa.Column("session_id", sa.UUID(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False), sa.Column("proposal", postgresql.JSONB(), nullable=False), sa.Column("validation", postgresql.JSONB()), sa.Column("profile_fingerprint", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"), sa.UniqueConstraint("session_id", "revision", name="uq_proposal_revision"))
    op.create_index("ix_proposal_revisions_session_id", "proposal_revisions", ["session_id"])


def downgrade() -> None:
    op.drop_table("proposal_revisions")
    op.drop_table("tool_invocations")
    op.drop_table("skill_invocations")
    op.drop_table("conversation_messages")
    op.drop_table("agent_sessions")

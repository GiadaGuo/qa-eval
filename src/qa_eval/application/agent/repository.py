from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.infrastructure.db.models.agent import (
    AgentSessionModel,
    ConversationMessageModel,
    ProposalRevisionModel,
    SkillInvocationModel,
    ToolInvocationModel,
)


class SQLAlchemyAgentSessionRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, model: object) -> None:
        self.session.add(model)

    def get_session(self, session_id: UUID) -> AgentSessionModel | None:
        return self.session.get(AgentSessionModel, session_id)

    def sessions_for_dataset_version(
        self, dataset_version_id: UUID
    ) -> list[AgentSessionModel]:
        statement = (
            select(AgentSessionModel)
            .where(AgentSessionModel.dataset_version_id == dataset_version_id)
            .order_by(
                AgentSessionModel.updated_at.desc(),
                AgentSessionModel.created_at.desc(),
                AgentSessionModel.session_id.desc(),
            )
        )
        return list(self.session.scalars(statement))

    def messages(self, session_id: UUID) -> list[ConversationMessageModel]:
        statement = (
            select(ConversationMessageModel)
            .where(ConversationMessageModel.session_id == session_id)
            .order_by(
                ConversationMessageModel.created_at,
                ConversationMessageModel.message_id,
            )
        )
        return list(self.session.scalars(statement))

    def skill_invocations(self, session_id: UUID) -> list[SkillInvocationModel]:
        statement = (
            select(SkillInvocationModel)
            .where(SkillInvocationModel.session_id == session_id)
            .order_by(SkillInvocationModel.started_at, SkillInvocationModel.invocation_id)
        )
        return list(self.session.scalars(statement))

    def tool_invocations(self, session_id: UUID) -> list[ToolInvocationModel]:
        statement = (
            select(ToolInvocationModel)
            .where(ToolInvocationModel.session_id == session_id)
            .order_by(ToolInvocationModel.started_at, ToolInvocationModel.invocation_id)
        )
        return list(self.session.scalars(statement))

    def proposal_revisions(self, session_id: UUID) -> list[ProposalRevisionModel]:
        statement = (
            select(ProposalRevisionModel)
            .where(ProposalRevisionModel.session_id == session_id)
            .order_by(ProposalRevisionModel.revision)
        )
        return list(self.session.scalars(statement))

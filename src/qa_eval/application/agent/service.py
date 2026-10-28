from uuid import UUID

from sqlalchemy.orm import Session

from qa_eval.application.agent.contracts import AgentSessionSnapshot
from qa_eval.application.agent.repository import SQLAlchemyAgentSessionRepository
from qa_eval.application.agent.runtime import AgentRuntime
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.models.agent import AgentSessionModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.llm.client import (
    OpenAICompatibleLLMClient,
    StructuredLLMClient,
)
from qa_eval.infrastructure.uuid7 import uuid7


class AgentSessionService:
    def __init__(
        self,
        session: Session,
        llm_client: StructuredLLMClient | None = None,
    ):
        self.session = session
        self.repository = SQLAlchemyAgentSessionRepository(session)
        self.llm_client = llm_client

    def create(self, dataset_version_id: UUID) -> AgentSessionModel:
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None:
            raise LookupError("DatasetVersion not found")
        if version.status != "PUBLISHED":
            raise ValueError("Agent Session requires a PUBLISHED DatasetVersion")
        model = AgentSessionModel(
            session_id=uuid7(),
            dataset_version_id=dataset_version_id,
            status="ACTIVE",
        )
        self.repository.add(model)
        self.session.flush()
        return model

    def snapshot(self, session_id: UUID) -> AgentSessionSnapshot | None:
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            return None
        revisions = self.repository.proposal_revisions(session_id)
        return AgentSessionSnapshot(
            session=agent_session,
            messages=self.repository.messages(session_id),
            skill_invocations=self.repository.skill_invocations(session_id),
            tool_invocations=self.repository.tool_invocations(session_id),
            proposal_revisions=revisions,
            active_proposal_revision=revisions[-1] if revisions else None,
        )

    def send_message(
        self,
        session_id: UUID,
        content: str,
        client_request_id: UUID,
    ):
        settings = get_settings()
        llm_client = self.llm_client
        if llm_client is None and settings.agent_llm_enabled:
            llm_client = OpenAICompatibleLLMClient.from_settings(settings)
        return AgentRuntime(
            self.session,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            max_tool_calls=settings.agent_max_tool_calls_per_turn,
            llm_client=llm_client,
        ).handle_message(
            session_id, content, client_request_id
        )

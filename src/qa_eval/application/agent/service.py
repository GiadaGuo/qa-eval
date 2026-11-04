from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from qa_eval.application.agent.contracts import AgentSessionSnapshot
from qa_eval.application.agent.repository import SQLAlchemyAgentSessionRepository
from qa_eval.application.agent.runtime import AgentRuntime
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.config import get_settings
from qa_eval.domain.exceptions import AgentSessionNotDeletableError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.agent import (
    AgentSessionModel,
    ProposalRevisionModel,
)
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel
from qa_eval.infrastructure.llm.client import (
    OpenAICompatibleLLMClient,
    StructuredLLMClient,
)
from qa_eval.infrastructure.uuid7 import uuid7

# 与 EvaluationRunService._DELETABLE_STATUSES / AgentRuntime 终态集合保持一致
_TERMINAL_RUN_STATUSES = {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED", "CANCELED"}


class AgentSessionService:
    def __init__(
        self,
        session: Session,
        llm_client: StructuredLLMClient | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.session = session
        self.repository = SQLAlchemyAgentSessionRepository(session)
        self.llm_client = llm_client
        self.artifact_store = artifact_store

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

    def list_for_dataset_version(
        self, dataset_version_id: UUID
    ) -> list[AgentSessionModel]:
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None:
            raise LookupError("DatasetVersion not found")
        return self.repository.sessions_for_dataset_version(dataset_version_id)

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

    def delete(self, session_id: UUID) -> bool:
        """物理删除 AgentSession 及其会话专属数据。

        - 会话不存在时返回 False（路由转换为 404，重复删除幂等）。
        - 关联 EvaluationRun 未到终态、或 Skill / Tool 调用仍在运行时抛
          AgentSessionNotDeletableError（路由转换为 409）。
        - Quality Contract、Evaluation Run、Metric Result、Quality Report
          等独立业务资产没有指向 agent_sessions 的外键，不受删除影响；
          消息、调用轨迹与未发布 Proposal 草稿按外键 CASCADE 一并删除。
        """
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            return False
        for message in self.repository.messages(session_id):
            for attachment in message.attachments:
                if attachment.get("type") != "EVALUATION_RUN":
                    continue
                run_id = attachment.get("run_id")
                if not run_id:
                    continue
                run = self.session.get(EvaluationRunModel, UUID(str(run_id)))
                if run is not None and run.status not in _TERMINAL_RUN_STATUSES:
                    raise AgentSessionNotDeletableError(status=run.status)
        if self.repository.running_invocation_exists(session_id):
            raise AgentSessionNotDeletableError(status="RUNNING")
        self.session.delete(agent_session)
        self.session.flush()
        return True

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
            artifact_store=self.artifact_store,
        ).handle_message(
            session_id, content, client_request_id
        )

    def update_proposal(self, session_id: UUID, proposal_revision_id: UUID, proposal):
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            raise LookupError("AgentSession not found")
        if agent_session.status != "ACTIVE":
            raise ValueError("AgentSession is not ACTIVE")
        row = self.session.get(ProposalRevisionModel, proposal_revision_id)
        if row is None or row.session_id != session_id:
            raise LookupError("Proposal revision not found in AgentSession")
        if str(proposal.proposal_id) != str(row.proposal.get("proposal_id")) or proposal.revision != row.revision:
            raise ValueError("Proposal identity and revision are immutable")
        if proposal.dataset_version_id != agent_session.dataset_version_id:
            raise ValueError("Proposal DatasetVersion does not match AgentSession")
        validation = EvaluationProposalService(self.session).validate(proposal)
        row.proposal = proposal.model_dump(mode="json")
        row.validation = validation.model_dump(mode="json")
        agent_session.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def confirm_contract(
        self,
        session_id: UUID,
        proposal_revision_id: UUID,
        *,
        name: str,
        version_label: str,
        client_request_id: UUID,
    ):
        settings = get_settings()
        return AgentRuntime(
            self.session,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            max_tool_calls=settings.agent_max_tool_calls_per_turn,
            artifact_store=self.artifact_store,
        ).confirm_quality_contract(
            session_id,
            proposal_revision_id,
            name=name,
            version_label=version_label,
            client_request_id=client_request_id,
        )

    def confirm_run(
        self,
        session_id: UUID,
        contract_version_id: UUID,
        *,
        client_request_id: UUID,
    ):
        settings = get_settings()
        return AgentRuntime(
            self.session,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            max_tool_calls=settings.agent_max_tool_calls_per_turn,
            artifact_store=self.artifact_store,
        ).confirm_evaluation_run(
            session_id,
            contract_version_id,
            client_request_id=client_request_id,
        )

    def observe_run(self, session_id: UUID, run_id: UUID):
        settings = get_settings()
        return AgentRuntime(
            self.session,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            max_tool_calls=settings.agent_max_tool_calls_per_turn,
            artifact_store=self.artifact_store,
        ).observe_evaluation_run(session_id, run_id)

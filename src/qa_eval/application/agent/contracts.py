from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentSessionCreate(BaseModel):
    dataset_version_id: UUID


class AgentMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    client_request_id: UUID


class ContractConfirmationCreate(BaseModel):
    proposal_revision_id: UUID
    confirmed: Literal[True]
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=64)
    client_request_id: UUID


class EvaluationRunConfirmationCreate(BaseModel):
    contract_version_id: UUID
    confirmed: Literal[True]
    client_request_id: UUID


class AgentSession(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    dataset_version_id: UUID
    status: Literal["ACTIVE", "COMPLETED", "FAILED"]
    active_skill: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: UUID
    session_id: UUID
    role: Literal["USER", "ASSISTANT", "SYSTEM", "TOOL"]
    content: str
    attachments: list[dict] = Field(default_factory=list)
    created_at: datetime


class SkillInvocation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invocation_id: UUID
    session_id: UUID
    skill_key: str
    skill_version: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    reason: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ToolInvocation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invocation_id: UUID
    session_id: UUID
    tool_name: str
    arguments_digest: str
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
    result_refs: list[dict] = Field(default_factory=list)
    error: dict | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ProposalRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proposal_revision_id: UUID
    session_id: UUID
    revision: int
    proposal: dict
    validation: dict | None = None
    profile_fingerprint: str
    created_at: datetime


class AgentSessionSnapshot(BaseModel):
    session: AgentSession
    messages: list[ConversationMessage]
    skill_invocations: list[SkillInvocation]
    tool_invocations: list[ToolInvocation]
    proposal_revisions: list[ProposalRevision]
    active_proposal_revision: ProposalRevision | None = None


class AgentMessageResult(BaseModel):
    message: ConversationMessage
    skill_invocation: SkillInvocation
    tool_invocations: list[ToolInvocation]


class ContractConfirmationResult(BaseModel):
    message: ConversationMessage
    tool_invocation: ToolInvocation
    contract_version_id: UUID
    contract_id: UUID
    item_count: int = Field(ge=1)


class EvaluationRunConfirmationResult(BaseModel):
    message: ConversationMessage
    tool_invocation: ToolInvocation
    run_id: UUID
    status: str


class EvaluationRunObservationResult(BaseModel):
    run_id: UUID
    status: str
    terminal: bool
    completion_message_created: bool = False
    report_id: UUID | None = None
    gate_status: str | None = None

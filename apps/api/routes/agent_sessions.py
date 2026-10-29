from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import get_agent_session_service
from apps.api.routes.evaluation_runs import get_evaluation_dispatcher
from qa_eval.application.agent.contracts import (
    AgentMessageCreate,
    AgentMessageResult,
    AgentSession,
    AgentSessionCreate,
    AgentSessionSnapshot,
    ContractConfirmationCreate,
    ContractConfirmationResult,
    EvaluationRunConfirmationCreate,
    EvaluationRunConfirmationResult,
    EvaluationRunObservationResult,
    ProposalDraftUpdate,
    ProposalRevision,
)
from qa_eval.application.agent.service import AgentSessionService

router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent-sessions"])
Service = Annotated[AgentSessionService, Depends(get_agent_session_service)]


@router.post("", response_model=AgentSession, status_code=201)
def create_agent_session(payload: AgentSessionCreate, service: Service):
    try:
        return service.create(payload.dataset_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=list[AgentSession])
def list_agent_sessions(dataset_version_id: UUID, service: Service):
    try:
        return service.list_for_dataset_version(dataset_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=AgentSessionSnapshot)
def get_agent_session(session_id: UUID, service: Service):
    snapshot = service.snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="AgentSession not found")
    return snapshot


@router.put(
    "/{session_id}/proposal-revisions/{proposal_revision_id}",
    response_model=ProposalRevision,
)
def update_proposal_draft(
    session_id: UUID,
    proposal_revision_id: UUID,
    payload: ProposalDraftUpdate,
    service: Service,
):
    try:
        return service.update_proposal(session_id, proposal_revision_id, payload.proposal)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/messages", response_model=AgentMessageResult, status_code=201)
def send_agent_message(
    session_id: UUID,
    payload: AgentMessageCreate,
    service: Service,
):
    try:
        return service.send_message(
            session_id, payload.content, payload.client_request_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{session_id}/contract-confirmations",
    response_model=ContractConfirmationResult,
    status_code=201,
)
def confirm_quality_contract(
    session_id: UUID,
    payload: ContractConfirmationCreate,
    service: Service,
):
    try:
        return service.confirm_contract(
            session_id,
            payload.proposal_revision_id,
            name=payload.name,
            version_label=payload.version_label,
            client_request_id=payload.client_request_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{session_id}/evaluation-runs",
    response_model=EvaluationRunConfirmationResult,
    status_code=202,
)
def confirm_evaluation_run(
    session_id: UUID,
    payload: EvaluationRunConfirmationCreate,
    service: Service,
    dispatcher: Annotated[Callable[[UUID], None], Depends(get_evaluation_dispatcher)],
):
    try:
        result = service.confirm_run(
            session_id,
            payload.contract_version_id,
            client_request_id=payload.client_request_id,
        )
        # The background worker uses a new DB session, so the Run and its Agent
        # audit trail must be committed before dispatch.
        service.session.commit()
        dispatcher(result.run_id)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/{session_id}/evaluation-runs/{run_id}/status",
    response_model=EvaluationRunObservationResult,
)
def observe_evaluation_run(session_id: UUID, run_id: UUID, service: Service):
    try:
        return service.observe_run(session_id, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

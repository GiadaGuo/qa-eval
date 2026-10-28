from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import get_agent_session_service
from qa_eval.application.agent.contracts import (
    AgentMessageCreate,
    AgentMessageResult,
    AgentSession,
    AgentSessionCreate,
    AgentSessionSnapshot,
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


@router.get("/{session_id}", response_model=AgentSessionSnapshot)
def get_agent_session(session_id: UUID, service: Service):
    snapshot = service.snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="AgentSession not found")
    return snapshot


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

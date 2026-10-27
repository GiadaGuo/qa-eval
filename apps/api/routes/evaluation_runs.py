from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.dependencies import get_artifact_store, get_evaluation_run_service
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.contracts.execution import EvaluationRunV1
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.infrastructure.db.session import SessionLocal

router = APIRouter(prefix="/api/v1/evaluation-runs", tags=["evaluation-runs"])
Service = Annotated[EvaluationRunService, Depends(get_evaluation_run_service)]
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-eval-runner")


class EvaluationRunCreate(BaseModel):
    contract_version_id: UUID


def _execute_in_background(run_id: UUID) -> None:
    with SessionLocal() as session:
        service = EvaluationRunService(session, get_artifact_store())
        service.execute(run_id)


def dispatch_evaluation_run(run_id: UUID) -> None:
    _executor.submit(_execute_in_background, run_id)


def get_evaluation_dispatcher() -> Callable[[UUID], None]:
    return dispatch_evaluation_run


@router.post("", response_model=EvaluationRunV1, status_code=202)
def create_run(
    payload: EvaluationRunCreate,
    service: Service,
    dispatcher: Annotated[Callable[[UUID], None], Depends(get_evaluation_dispatcher)],
):
    try:
        run = service.create(payload.contract_version_id)
        service.session.commit()
        dispatcher(run.run_id)
        return run
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[EvaluationRunV1])
def list_runs(service: Service):
    return service.list_runs()


@router.get("/{run_id}", response_model=EvaluationRunV1)
def get_run(run_id: UUID, service: Service):
    result = service.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="EvaluationRun not found")
    return result


@router.get("/{run_id}/metric-results", response_model=list[MetricResultV1])
def get_metric_results(run_id: UUID, service: Service):
    if service.get(run_id) is None:
        raise HTTPException(status_code=404, detail="EvaluationRun not found")
    return service.list_results(run_id)

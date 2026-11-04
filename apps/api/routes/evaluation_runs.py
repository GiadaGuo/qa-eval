import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.dependencies import get_artifact_store, get_evaluation_run_service
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.calculators.protocol import CalculatorError
from qa_eval.contracts.execution import (
    EvaluationRunV1,
    LogicalEvaluationPlanV1,
    StageRunV1,
)
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.domain.exceptions import EvaluationRunNotDeletableError
from qa_eval.infrastructure.db.session import SessionLocal

router = APIRouter(prefix="/api/v1/evaluation-runs", tags=["evaluation-runs"])
Service = Annotated[EvaluationRunService, Depends(get_evaluation_run_service)]
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-eval-runner")
EVALUATION_WORKER_ID = str(uuid4())
logger = logging.getLogger(__name__)


class EvaluationRunCreate(BaseModel):
    contract_version_id: UUID


def _execute_in_background(run_id: UUID) -> None:
    with SessionLocal() as session:
        service = EvaluationRunService(session, get_artifact_store())
        try:
            service.execute(run_id, worker_id=EVALUATION_WORKER_ID)
        except Exception as exc:
            logger.exception(
                "Evaluation Run background execution crashed: run_id=%s", run_id
            )
            try:
                service.fail_running_run(run_id, exc)
            except Exception:
                logger.exception(
                    "Failed to persist Evaluation Run crash state: run_id=%s", run_id
                )
        finally:
            close = getattr(service.llm_client, "close", None)
            if callable(close):
                close()


def dispatch_evaluation_run(run_id: UUID) -> None:
    try:
        _executor.submit(_execute_in_background, run_id)
    except Exception as exc:
        with SessionLocal() as session:
            EvaluationRunService(session, get_artifact_store()).fail_running_run(
                run_id, exc
            )
        raise


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
        run = service.claim_pending_run(run.run_id, worker_id=EVALUATION_WORKER_ID)
        dispatcher(run.run_id)
        return run
    except (ValueError, CalculatorError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[EvaluationRunV1])
def list_runs(service: Service):
    return service.list_runs()


@router.get("/logical-plans/{logical_plan_id}", response_model=LogicalEvaluationPlanV1)
def get_logical_plan(logical_plan_id: UUID, service: Service):
    result = service.get_logical_plan(logical_plan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="LogicalEvaluationPlan not found")
    return result


@router.get(
    "/logical-plans/by-contract/{contract_version_id}",
    response_model=LogicalEvaluationPlanV1,
)
def get_logical_plan_by_contract(contract_version_id: UUID, service: Service):
    result = service.logical_plan_for_contract(contract_version_id)
    if result is None:
        raise HTTPException(status_code=404, detail="LogicalEvaluationPlan not found")
    return result


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


@router.get("/{run_id}/stage-runs", response_model=list[StageRunV1])
def get_stage_runs(run_id: UUID, service: Service):
    if service.get(run_id) is None:
        raise HTTPException(status_code=404, detail="EvaluationRun not found")
    return service.list_stage_runs(run_id)


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: UUID, service: Service):
    try:
        deleted = service.delete_run(run_id)
    except EvaluationRunNotDeletableError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "status": exc.status,
            },
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="EvaluationRun not found")

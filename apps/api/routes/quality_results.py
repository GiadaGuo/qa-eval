from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.dependencies import get_quality_result_service
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.quality import (
    QualityReportV1,
    QualityReportV1_1,
    RuleEvaluationV1,
)

router = APIRouter(prefix="/api/v1", tags=["quality-results"])
Service = Annotated[QualityResultService, Depends(get_quality_result_service)]


@router.get("/quality-reports", response_model=list[QualityReportV1 | QualityReportV1_1])
def list_reports(service: Service):
    return service.list_reports()


@router.get(
    "/evaluation-runs/{run_id}/quality-report",
    response_model=QualityReportV1 | QualityReportV1_1,
)
def get_report(run_id: UUID, service: Service):
    report = service.get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="QualityReport not found")
    return report


@router.post(
    "/evaluation-runs/{run_id}/finalize",
    response_model=QualityReportV1 | QualityReportV1_1,
)
def finalize_run(run_id: UUID, service: Service):
    """Idempotently (re-)generate the QualityReport of a terminal EvaluationRun.

    Compensates runs whose inline finalization never ran or failed, e.g. before
    the quality-results schema existed; an existing report is returned as-is.
    """
    if not service.has_run(run_id):
        raise HTTPException(status_code=404, detail="EvaluationRun not found")
    try:
        report = service.finalize(run_id)
        service.session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report


@router.get(
    "/evaluation-runs/{run_id}/rule-evaluations",
    response_model=list[RuleEvaluationV1],
)
def list_rules(run_id: UUID, service: Service):
    return service.list_rules(run_id)


@router.get("/evaluation-runs/{run_id}/quality-issues")
def list_issues(
    run_id: UUID,
    service: Service,
    determination: Literal["CONFIRMED", "CANDIDATE"] | None = None,
    metric_result_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, issues = service.list_issues(
        run_id,
        determination=determination,
        metric_result_id=metric_result_id,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }

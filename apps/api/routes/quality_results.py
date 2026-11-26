from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.dependencies import (
    get_evidence_query_service,
    get_evaluation_run_service,
    get_document_llm_client,
    get_quality_result_service,
)
from qa_eval.application.evaluation.unified_report import QualityReportV2
from qa_eval.application.evidence.index import IndexNotReadyError
from qa_eval.application.services.report_interpretation import report_interpretation_jobs
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.quality import (
    NativeRuleEvaluationV1,
    QualityReportV1,
    QualityReportV1_1,
    RuleEvaluationV1,
)
from qa_eval.contracts.scoring import OperationalScoringReportV1

router = APIRouter(prefix="/api/v1", tags=["quality-results"])
Service = Annotated[QualityResultService, Depends(get_quality_result_service)]
EvidenceService = Annotated[EvidenceQueryService, Depends(get_evidence_query_service)]


@router.get(
    "/quality-reports", response_model=list[QualityReportV1 | QualityReportV1_1 | QualityReportV2]
)
def list_reports(service: Service):
    return service.list_reports()


class ReportCombinationRequest(BaseModel):
    dataset_version_id: UUID
    document_report_id: UUID
    applicability_confirmed: Literal[True]


@router.get("/quality-reports/{report_id}", response_model=QualityReportV1 | QualityReportV1_1 | QualityReportV2)
def get_report_snapshot(report_id: UUID, service: Service):
    report = service.get_report_snapshot(report_id)
    if report is None:
        raise HTTPException(404, "QualityReport not found")
    return report


@router.post("/evaluation-runs/{run_id}/combined-quality-report", response_model=QualityReportV2)
def combine_report(run_id: UUID, payload: ReportCombinationRequest, service: Service):
    try:
        report = service.combine_report(run_id, payload.document_report_id, payload.dataset_version_id)
        service.session.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return report


@router.get(
    "/evaluation-runs/{run_id}/quality-report",
    response_model=QualityReportV1 | QualityReportV1_1 | QualityReportV2,
)
def get_report(run_id: UUID, service: Service):
    report = service.get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="QualityReport not found")
    return report


@router.get("/evaluation-runs/{run_id}/report-interpretation")
def get_report_interpretation(run_id: UUID, service: Service):
    return report_interpretation_jobs.status(get_report(run_id, service).report_id)


@router.post("/evaluation-runs/{run_id}/report-interpretation", status_code=202)
def generate_report_interpretation(
    run_id: UUID,
    service: Service,
    runs: Annotated[EvaluationRunService, Depends(get_evaluation_run_service)],
    regenerate: bool = False,
):
    report = get_report(run_id, service)
    facts = {}
    if isinstance(report, QualityReportV2):
        facts["总体判定"] = report.overall_verdict
        facts["问题数量"] = report.issue_counts
        for dimension in report.dimensions:
            facts[f"维度 {dimension.key}"] = {
                key: value for key, value in dimension.model_dump(mode="json").items()
                if key in {"score", "verdict", "coverage"}
            }
    else:
        facts["质量门禁"] = report.gate_status
        facts["确认违规"] = report.issue_summary.confirmed_count
        facts["任务执行"] = report.coverage.model_dump(mode="json")
        if isinstance(report, QualityReportV1_1):
            facts["业务评分"] = report.scoring.model_dump(
                mode="json", include={"overall_score", "overall_score_status", "partial_score", "policy"}
            )
            for section in report.native_evaluations:
                rule = section.evaluation
                facts[f"门禁 {rule.statistic_ref.contract_item_key}"] = {
                    "状态": rule.status, "实际值": rule.metric_value,
                    "比较方式": rule.rule.operator, "阈值": rule.rule.expected,
                    "分子": rule.numerator, "分母": rule.denominator,
                }
    for result in runs.list_calculator_results(run_id):
        for key, value in result.statistics.items():
            fact_key = f"{result.contract_item_key}.{key}"
            if isinstance(value, (int, float)):
                facts[fact_key] = value
            elif key == "unknown_reasons" and result.calculator_key == "policy_document_format" and isinstance(value, dict):
                facts[fact_key] = {
                    code: count for code, count in value.items() if isinstance(count, int)
                }
    try:
        report_interpretation_jobs.start(
            report.report_id, facts, get_document_llm_client, regenerate=regenerate
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return report_interpretation_jobs.status(report.report_id)


@router.post(
    "/evaluation-runs/{run_id}/finalize",
    response_model=QualityReportV1 | QualityReportV1_1 | QualityReportV2,
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
    "/evaluation-runs/{run_id}/quality-report/operational-scoring",
    response_model=OperationalScoringReportV1,
)
def get_operational_scoring_report(
    run_id: UUID,
    service: Service,
    profile_id: str = Query(default="DATA_QUALITY_MVP_SCORE_V1"),
):
    report = service.get_operational_report(run_id, profile_id=profile_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail="Operational scoring report not found"
        )
    return report


@router.post(
    "/evaluation-runs/{run_id}/quality-report/operational-scoring/rebuild",
    response_model=OperationalScoringReportV1,
)
def rebuild_operational_scoring_report(
    run_id: UUID,
    service: Service,
    profile_id: str = Query(default="DATA_QUALITY_MVP_SCORE_V1"),
):
    if not service.has_run(run_id):
        raise HTTPException(status_code=404, detail="EvaluationRun not found")
    try:
        report = service.rebuild_operational_report(run_id, profile_id=profile_id)
        service.session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report


@router.get(
    "/evaluation-runs/{run_id}/rule-evaluations",
    response_model=list[RuleEvaluationV1 | NativeRuleEvaluationV1],
)
def list_rules(run_id: UUID, service: Service):
    return service.list_rules(run_id)


@router.get("/evaluation-runs/{run_id}/quality-issues")
def list_issues(
    run_id: UUID,
    service: Service,
    determination: Literal["CONFIRMED", "CANDIDATE", "SUSPECTED"] | None = None,
    metric_result_id: UUID | None = None,
    calculator_result_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, issues = service.list_issues(
        run_id,
        determination=determination,
        metric_result_id=metric_result_id,
        calculator_result_id=calculator_result_id,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }


@router.get("/quality-issues/{issue_id}")
def get_issue(issue_id: UUID, service: EvidenceService):
    issue = service.get_issue(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "ISSUE_NOT_FOUND"})
    return issue.model_dump(mode="json")


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: UUID, service: EvidenceService):
    metadata = service.evidence_metadata(evidence_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail={"code": "EVIDENCE_NOT_FOUND"})
    metadata["index"] = _public_index(metadata["index"])
    return metadata


@router.post("/evidence/{evidence_id}/index/rebuild")
def rebuild_evidence_index(
    evidence_id: UUID,
    service: EvidenceService,
    force: bool = Query(default=False),
):
    try:
        metadata = service.build_evidence_index(evidence_id, force=force)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INDEX_BUILD_FAILED", "message": str(exc)},
        ) from exc
    if metadata is None:
        raise HTTPException(status_code=404, detail={"code": "EVIDENCE_NOT_FOUND"})
    return _public_index(metadata)


@router.get("/evidence/{evidence_id}/rows")
def list_evidence_rows(
    evidence_id: UUID,
    service: EvidenceService,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    row_id: str | None = None,
    sample_id: str | None = None,
    content_id: str | None = None,
    fact_code: str | None = None,
    status: str | None = None,
    reason: str | None = None,
):
    try:
        result = service.list_evidence_rows(
            evidence_id,
            limit=limit,
            cursor=cursor,
            row_id=row_id,
            sample_id=sample_id,
            content_id=content_id,
            fact_code=fact_code,
            status=status,
            reason=reason,
        )
    except IndexNotReadyError as exc:
        raise HTTPException(
            status_code=409 if exc.status != "FAILED" else 503,
            detail={"code": f"INDEX_{exc.status}", "index_status": exc.status},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_EVIDENCE_QUERY", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "EVIDENCE_NOT_FOUND"})
    rows, next_cursor = result
    return {"rows": rows, "limit": limit, "next_cursor": next_cursor}


@router.get("/quality-issues/{issue_id}/evidence")
def get_issue_evidence(issue_id: UUID, service: EvidenceService):
    try:
        bundle = service.issue_evidence_rows(issue_id)
    except IndexNotReadyError as exc:
        raise HTTPException(
            status_code=409 if exc.status != "FAILED" else 503,
            detail={"code": f"INDEX_{exc.status}", "index_status": exc.status},
        ) from exc
    if bundle is None:
        raise HTTPException(status_code=404, detail={"code": "ISSUE_NOT_FOUND"})
    return bundle


@router.get("/quality-issues/{issue_id}/locations")
def get_issue_locations(issue_id: UUID, service: EvidenceService):
    try:
        bundle = service.issue_locations(issue_id)
    except IndexNotReadyError as exc:
        raise HTTPException(
            status_code=409 if exc.status != "FAILED" else 503,
            detail={"code": f"INDEX_{exc.status}", "index_status": exc.status},
        ) from exc
    if bundle is None:
        raise HTTPException(status_code=404, detail={"code": "ISSUE_NOT_FOUND"})
    return bundle


def _public_index(metadata: dict) -> dict:
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"source_uri", "index_uri", "metadata_uri"}
    }

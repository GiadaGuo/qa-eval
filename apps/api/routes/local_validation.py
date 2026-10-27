# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 闭环 1：LOCAL_VALIDATION 产物只读 API。
# 返回已有真实 Dataset Version、MetricResult、Rule、Issue 和 Report，
# 明确标识为本地验证，不作为正式平台运行。
# ====================================================

from fastapi import APIRouter, HTTPException

from qa_eval.application.services.local_validation_service import (
    LOCAL_VALIDATION_AUTHORITY,
    LocalValidationService,
)

router = APIRouter(
    prefix="/api/v1/local-validation",
    tags=["local-validation"],
)


def _get_service() -> LocalValidationService:
    return LocalValidationService()


@router.get("")
def get_local_validation_summary():
    """返回 LOCAL_VALIDATION 产物摘要与可用资源索引。"""
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load LOCAL_VALIDATION artifacts: {exc}",
        ) from exc

    return {
        "authority": artifacts.authority,
        "not_a_platform_run": artifacts.not_a_platform_run,
        "run_dir": str(artifacts.run_dir),
        "dataset_version_id": str(artifacts.manifest.dataset_version_id),
        "dataset_name": artifacts.manifest.name,
        "metric_key": artifacts.metric_result.metric.key,
        "metric_version": artifacts.metric_result.metric.version,
        "contract_version_id": str(artifacts.contract.contract_version_id),
        "report_id": str(artifacts.report.report_id),
        "gate_status": artifacts.report.gate_status,
        "execution_status": artifacts.report.execution_status,
        "issue_summary": {
            "confirmed_count": artifacts.report.issue_summary.confirmed_count,
            "candidate_count": artifacts.report.issue_summary.candidate_count,
            "open_count": artifacts.report.issue_summary.open_count,
        },
        "links": {
            "manifest": "/api/v1/local-validation/manifest",
            "metric_result": "/api/v1/local-validation/metric-result",
            "contract": "/api/v1/local-validation/contract",
            "rule_evaluations": "/api/v1/local-validation/rule-evaluations",
            "issues": "/api/v1/local-validation/issues",
            "issue_index": "/api/v1/local-validation/issue-index",
            "report": "/api/v1/local-validation/report",
        },
    }


@router.get("/manifest")
def get_local_validation_manifest():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "manifest": artifacts.manifest.model_dump(mode="json"),
    }


@router.get("/metric-result")
def get_local_validation_metric_result():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "metric_result": artifacts.metric_result.model_dump(mode="json"),
    }


@router.get("/contract")
def get_local_validation_contract():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "contract": artifacts.contract.model_dump(mode="json"),
    }


@router.get("/rule-evaluations")
def get_local_validation_rule_evaluations():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "rule_evaluations": [
            item.model_dump(mode="json") for item in artifacts.rule_evaluations
        ],
    }


@router.get("/issues")
def get_local_validation_issues(
    determination: str | None = None,
    severity: str | None = None,
    scope: str | None = None,
    code: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """分页返回 LOCAL_VALIDATION Issues，支持按 determination/severity/scope/code 筛选。"""
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    issues = artifacts.issues
    if determination:
        issues = [item for item in issues if item.determination == determination]
    if severity:
        issues = [item for item in issues if item.severity == severity]
    if scope:
        issues = [item for item in issues if item.scope == scope]
    if code:
        issues = [item for item in issues if item.code == code]

    total = len(issues)
    page = issues[offset : offset + limit]

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "issues": [item.model_dump(mode="json") for item in page],
    }


@router.get("/issue-index")
def get_local_validation_issue_index():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "issue_index": artifacts.issue_index.model_dump(mode="json"),
    }


@router.get("/report")
def get_local_validation_report():
    service = _get_service()
    try:
        artifacts = service.load()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "authority": LOCAL_VALIDATION_AUTHORITY,
        "not_a_platform_run": True,
        "report": artifacts.report.model_dump(mode="json"),
    }

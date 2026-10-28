# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档质量报告查询路由（报告随任务产出，只读）。
# ====================================================

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import get_document_report_task_service
from qa_eval.application.services.document_report_task_service import (
    DocumentReportTaskService,
)
from qa_eval.contracts.document import DocumentQualityReportV1

router = APIRouter(
    prefix="/api/v1/document-quality-reports",
    tags=["document-quality-reports"],
)
Service = Annotated[
    DocumentReportTaskService, Depends(get_document_report_task_service)
]


@router.get("/{report_id}", response_model=DocumentQualityReportV1)
def get_report(report_id: UUID, service: Service) -> DocumentQualityReportV1:
    report = service.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Document quality report not found")
    return report

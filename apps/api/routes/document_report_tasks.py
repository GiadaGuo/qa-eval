# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 说明文档评测任务路由：创建即派发后台线程执行（镜像
# evaluation_runs 的 ThreadPoolExecutor 模式），任务与报告查询。
# ====================================================

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.dependencies import (
    get_artifact_store,
    get_document_llm_client,
    get_document_report_task_service,
    get_settings,
)
from qa_eval.application.services.document_report_task_service import (
    DocumentReportTaskService,
)
from qa_eval.config import Settings
from qa_eval.infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/document-report-tasks", tags=["document-report-tasks"]
)
Service = Annotated[
    DocumentReportTaskService, Depends(get_document_report_task_service)
]
_executor = ThreadPoolExecutor(
    max_workers=Settings().document_executor_workers,
    thread_name_prefix="qa-eval-document-runner",
)


class DocumentReportTaskCreate(BaseModel):
    document_version_id: UUID


def _execute_in_background(task_id: UUID) -> None:
    try:
        with SessionLocal() as session:
            service = DocumentReportTaskService(
                session,
                get_artifact_store(),
                get_settings(),
                get_document_llm_client(),
            )
            service.execute(task_id)
    except Exception:
        logger.exception(
            "Document report task background execution crashed: task_id=%s",
            task_id,
        )
        raise


def dispatch_document_report_task(task_id: UUID) -> None:
    _executor.submit(_execute_in_background, task_id)


def get_document_report_dispatcher() -> Callable[[UUID], None]:
    return dispatch_document_report_task


@router.post("", status_code=202)
def create_task(
    payload: DocumentReportTaskCreate,
    service: Service,
    dispatcher: Annotated[
        Callable[[UUID], None], Depends(get_document_report_dispatcher)
    ],
    # LLM 网关未配置时创建入口直接 503，任务创建本身不依赖客户端
    _llm: Annotated[Any, Depends(get_document_llm_client)],
) -> dict:
    try:
        view = service.create_task(payload.document_version_id)
        service.session.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dispatcher(UUID(view["task_id"]))
    return view


@router.get("")
def list_tasks(
    service: Service,
    document_version_id: UUID | None = None,
) -> list[dict]:
    return service.list_tasks(document_version_id)


@router.get("/{task_id}")
def get_task(task_id: UUID, service: Service) -> dict:
    view = service.get_task(task_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Document report task not found")
    return view

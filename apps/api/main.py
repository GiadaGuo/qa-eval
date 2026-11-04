# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 11:27:43
# @Description:
# - Application Factory 模式，即通过 create_app() 创建应用
# - 后续可以为测试环境构造独立应用，也方便注入不同的数据库、对象存储和队列实现
# ====================================================

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.dependencies import get_artifact_store
from apps.api.routes.agent_sessions import router as agent_sessions_router
from apps.api.routes.dataset_versions import (
    router as dataset_versions_router,
)
from apps.api.routes.datasets import router as datasets_router
from apps.api.routes.document_quality_reports import (
    router as document_quality_reports_router,
)
from apps.api.routes.document_report_tasks import (
    router as document_report_tasks_router,
)
from apps.api.routes.document_versions import (
    router as document_versions_router,
)
from apps.api.routes.evaluation_proposals import router as evaluation_proposals_router
from apps.api.routes.evaluation_runs import (
    EVALUATION_WORKER_ID,
)
from apps.api.routes.evaluation_runs import (
    router as evaluation_runs_router,
)

# 注册路由
from apps.api.routes.health import router as health_router
from apps.api.routes.local_validation import (
    router as local_validation_router,
)
from apps.api.routes.mysql_datasource import (
    router as mysql_datasource_router,
)
from apps.api.routes.quality_contracts import router as quality_contracts_router
from apps.api.routes.quality_results import router as quality_results_router
from apps.api.routes.registry import router as registry_router
from qa_eval.application.services.document_report_task_service import (
    DocumentReportTaskService,
)
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动清扫：孤儿 RUNNING 任务置 FAILED（D5），best-effort
    try:
        with SessionLocal() as session:
            DocumentReportTaskService(
                session, get_artifact_store(), get_settings()
            ).sweep_interrupted()
    except Exception:
        logger.warning(
            "Document report task startup sweep skipped",
            exc_info=True,
        )
    try:
        with SessionLocal() as session:
            from qa_eval.application.services.evaluation_run_service import (
                EvaluationRunService,
            )

            EvaluationRunService(session, get_artifact_store()).sweep_interrupted(
                current_worker_id=EVALUATION_WORKER_ID
            )
    except Exception:
        logger.warning(
            "Evaluation Run startup sweep skipped",
            exc_info=True,
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(
        health_router,
        prefix=settings.api_prefix,
    )

    app.include_router(datasets_router)
    app.include_router(agent_sessions_router)

    app.include_router(dataset_versions_router)

    app.include_router(document_versions_router)

    app.include_router(local_validation_router)

    app.include_router(mysql_datasource_router)
    app.include_router(registry_router)
    app.include_router(quality_contracts_router)
    app.include_router(evaluation_runs_router)
    app.include_router(evaluation_proposals_router)
    app.include_router(quality_results_router)
    app.include_router(document_report_tasks_router)
    app.include_router(document_quality_reports_router)

    return app


app = create_app()

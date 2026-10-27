# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 11:27:43
# @Description:
# - Application Factory 模式，即通过 create_app() 创建应用
# - 后续可以为测试环境构造独立应用，也方便注入不同的数据库、对象存储和队列实现
# ====================================================

from fastapi import FastAPI

from apps.api.routes.dataset_versions import (
    router as dataset_versions_router,
)
from apps.api.routes.datasets import router as datasets_router
from apps.api.routes.evaluation_runs import router as evaluation_runs_router

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
from qa_eval.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    app.include_router(
        health_router,
        prefix=settings.api_prefix,
    )

    app.include_router(datasets_router)

    app.include_router(dataset_versions_router)

    app.include_router(local_validation_router)

    app.include_router(mysql_datasource_router)
    app.include_router(registry_router)
    app.include_router(quality_contracts_router)
    app.include_router(evaluation_runs_router)
    app.include_router(quality_results_router)

    return app


app = create_app()

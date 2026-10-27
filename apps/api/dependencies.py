# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:28:23
# @LastEditTime: 2026-10-22 02:26:50
# @Description: 
# FastAPI 依赖注入
# 1. PostgreSQL：平台元数据事务管理与业务服务
# 2. MySQL：外部数据源只读访问
# ====================================================

import os
from collections.abc import Generator

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.session import SessionLocal
from qa_eval.infrastructure.mysql.datasource_reader import (
    MySQLDataSourceReader,
)

load_dotenv()

# ====================================================
# 1. PostgreSQL 平台元数据库依赖
# ====================================================
def get_db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_dataset_service(
    session: Session = Depends(get_db_session),
) -> DatasetService:
    repository = SQLAlchemyDatasetRepository(session)
    return DatasetService(repository)

# ====================================================
# 2. MySQL 外部数据源依赖
# ====================================================

def get_mysql_reader() -> MySQLDataSourceReader:
    """创建 MySQL 数据源 Reader。"""

    required_keys = (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_TABLE",
    )

    missing = [
        key
        for key in required_keys
        if not os.getenv(key)
    ]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MySQL datasource is not configured",
        )

    try:
        return MySQLDataSourceReader(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ["MYSQL_PORT"]),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"],
            table=os.environ["MYSQL_TABLE"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid MySQL datasource configuration",
        ) from exc

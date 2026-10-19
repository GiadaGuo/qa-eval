# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:28:23
# @LastEditTime: 2026-10-19 16:29:19
# @Description: 实现的是一个请求级事务边界：
# - 请求成功：commit()。
# - 请求抛出异常：rollback()。
# - 请求结束：关闭 Session。
# 目前适用于我们的小型同步 CRUD 接口。
# 后续涉及 MinIO 文件写入和 PostgreSQL 元数据同时更新时，需要专门处理跨存储的一致性，不能误以为数据库事务也能回滚对象存储操作。
# ====================================================

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.session import SessionLocal


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

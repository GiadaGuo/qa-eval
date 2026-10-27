# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-22 02:03:34
# @LastEditTime: 2026-10-22 09:35:54
# @Description: 
# ====================================================
import logging
import time

logger = logging.getLogger(__name__)
from typing import Annotated, Any

import mysql.connector
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.dependencies import get_mysql_reader
from qa_eval.infrastructure.mysql.datasource_reader import (
    MySQLDataSourceReader,
)

router = APIRouter(
    prefix="/api/v1/datasources/mysql",
    tags=["MySQL DataSource"],
)

ReaderDep = Annotated[
    MySQLDataSourceReader,
    Depends(get_mysql_reader),
]


@router.get("/schema")
def get_mysql_schema(
    reader: ReaderDep,
) -> dict[str, Any]:
    """获取 MySQL 数据表 Schema。"""

    try:
        return reader.get_schema()

    except mysql.connector.Error as exc:
        raise HTTPException(
            status_code=502,
            detail="Failed to read MySQL schema",
        ) from exc


@router.get("/preview")
def get_mysql_preview(
    reader: ReaderDep,
    limit: int = Query(default=5, ge=1, le=20),
    max_text_length: int = Query(default=300, ge=1, le=2000),
) -> dict[str, Any]:
    """获取 MySQL 数据样本预览。"""

    start = time.perf_counter()
    logger.warning("Preview route entered: limit=%s", limit)

    try:
        result = reader.get_preview(
            limit=limit,
            max_text_length=max_text_length,
        )

        logger.warning(
            "Preview reader completed: elapsed=%.3fs, rows=%s",
            time.perf_counter() - start,
            len(result["rows"]),
        )

        return result

    except mysql.connector.Error as exc:
        logger.exception("Preview MySQL query failed")
        raise HTTPException(
            status_code=502,
            detail="Failed to read MySQL preview",
        ) from exc
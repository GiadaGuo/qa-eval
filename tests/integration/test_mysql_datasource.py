# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-22 02:32:53
# @LastEditTime: 2026-10-22 02:34:50
# @Description: 测试直接连接 Docker MySQL 中的 qa_eval_demo.jhsjk_article
# - 显式设置 RUN_MYSQL_INTEGRATION=1 才会执行
# ====================================================

import os
from datetime import date, datetime

import pytest
from dotenv import load_dotenv

from qa_eval.infrastructure.mysql.datasource_reader import (
    MySQLDataSourceReader,
)

# ====================================================
# 1. 测试环境配置
# ====================================================

load_dotenv()

# 默认跳过真实 MySQL 集成测试
# 显式设置 RUN_MYSQL_INTEGRATION=1 才会执行
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MYSQL_INTEGRATION") != "1",
    reason=(
        "MySQL integration tests require "
        "RUN_MYSQL_INTEGRATION=1"
    ),
)


@pytest.fixture
def mysql_reader() -> MySQLDataSourceReader:
    """创建指向本地 Docker MySQL 的 Reader。"""

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
        pytest.fail(
            "Missing MySQL configuration: "
            + ", ".join(missing)
        )

    return MySQLDataSourceReader(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        table=os.environ["MYSQL_TABLE"],
    )


# ====================================================
# 2. Schema 集成测试
# ====================================================

def test_mysql_schema(mysql_reader):
    result = mysql_reader.get_schema()

    assert result["database"] == "qa_eval_demo"
    assert result["table"] == "jhsjk_article"
    assert result["row_count"] == 20

    fields = result["fields"]

    assert len(fields) == 10

    field_names = {
        field["name"]
        for field in fields
    }

    assert {
        "id",
        "article_id",
        "title",
        "url",
        "pub_date",
        "origin_name",
        "summary",
        "content",
        "plain_text_content",
        "create_time",
    } == field_names

    primary_keys = [
        field["name"]
        for field in fields
        if field["is_primary_key"]
    ]

    assert primary_keys == ["id"]


# ====================================================
# 3. Preview 集成测试
# ====================================================

def test_mysql_preview_limit(mysql_reader):
    result = mysql_reader.get_preview(limit=5)

    assert result["limit"] == 5
    assert len(result["rows"]) == 5

    for row in result["rows"]:
        assert "id" in row
        assert "content" in row
        assert "plain_text_content" in row


def test_mysql_preview_date_serialization(
    mysql_reader,
):
    result = mysql_reader.get_preview(limit=20)

    rows = result["rows"]

    for row in rows:
        for field in ("pub_date", "create_time"):
            value = row[field]

            if value is not None:
                assert isinstance(value, str)
                assert not isinstance(
                    value,
                    (date, datetime),
                )

                # DATE / DATETIME 可由 ISO 格式解析
                if field == "pub_date":
                    date.fromisoformat(value)
                else:
                    datetime.fromisoformat(value)


def test_mysql_preview_preserves_null(
    mysql_reader,
):
    result = mysql_reader.get_preview(limit=20)

    rows = result["rows"]

    # 种子数据中有 2 条 content 为 NULL
    null_content_count = sum(
        row["content"] is None
        for row in rows
    )

    assert null_content_count == 2


def test_mysql_preview_truncates_text(
    mysql_reader,
):
    result = mysql_reader.get_preview(
        limit=20,
        max_text_length=10,
    )

    rows = result["rows"]

    # 只检查非空文本
    for row in rows:
        for field in (
            "title",
            "summary",
            "content",
            "plain_text_content",
        ):
            value = row[field]

            if value is not None:
                assert len(value) <= 11

    # 至少有一条正文实际发生截断
    assert any(
        row["content"] is not None
        and row["content"].endswith("…")
        for row in rows
    )


# ====================================================
# 4. 资源管理测试
# ====================================================

def test_mysql_connection_closed(
    mysql_reader,
    monkeypatch,
):
    """
    验证 Reader 调用完成后关闭连接。

    这里保留真实 MySQL 连接，
    仅包装 close() 用于观察关闭行为。
    """

    original_connect = mysql_reader._connect

    connections = []

    def tracked_connect():
        connection = original_connect()
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        mysql_reader,
        "_connect",
        tracked_connect,
    )

    mysql_reader.get_preview(limit=1)
    mysql_reader.get_schema()

    assert len(connections) == 2

    for connection in connections:
        assert not connection.is_connected()

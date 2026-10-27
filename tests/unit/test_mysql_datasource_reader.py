
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qa_eval.infrastructure.mysql.datasource_reader import (
    MySQLDataSourceReader,
)


@pytest.fixture
def reader():
    return MySQLDataSourceReader(
        host="127.0.0.1",
        port=3307,
        user="test_user",
        password="test_password",
        database="qa_eval_demo",
        table="jhsjk_article",
    )


def test_invalid_table_identifier():
    with pytest.raises(ValueError, match="Invalid table"):
        MySQLDataSourceReader(
            host="localhost",
            port=3306,
            user="test",
            password="test",
            database="qa_eval_demo",
            table="article; DROP TABLE users",
        )


@pytest.mark.parametrize("limit", [0, -1, 21])
def test_preview_rejects_invalid_limit(reader, limit):
    with pytest.raises(ValueError, match="limit"):
        reader.get_preview(limit=limit)


@pytest.mark.parametrize("length", [0, -1, 2001])
def test_preview_rejects_invalid_text_length(
    reader, length
):
    with pytest.raises(
        ValueError,
        match="max_text_length",
    ):
        reader.get_preview(max_text_length=length)


def test_schema_returns_fields_and_row_count(
    reader, monkeypatch
):
    connection = MagicMock()
    cursor = MagicMock()

    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = (20,)

    monkeypatch.setattr(
        reader, "_connect", lambda: connection
    )

    fields = [
        SimpleNamespace(
            name="id",
            data_type="char",
            nullable=False,
            is_primary_key=True,
        ),
        SimpleNamespace(
            name="content",
            data_type="longtext",
            nullable=True,
            is_primary_key=False,
        ),
    ]

    monkeypatch.setattr(
        "qa_eval.infrastructure.mysql.datasource_reader."
        "MySQLSchemaInspector.inspect",
        lambda self, database, table: fields,
    )

    result = reader.get_schema()

    assert result["database"] == "qa_eval_demo"
    assert result["table"] == "jhsjk_article"
    assert result["row_count"] == 20
    assert len(result["fields"]) == 2

    assert result["fields"][0] == {
        "name": "id",
        "data_type": "char",
        "nullable": False,
        "is_primary_key": True,
    }

    cursor.execute.assert_called_once_with(
        "SELECT COUNT(*) FROM "
        "`qa_eval_demo`.`jhsjk_article`"
    )

    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_preview_preserves_null_and_formats_values(
    reader, monkeypatch
):
    connection = MagicMock()
    cursor = MagicMock()

    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        {
            "id": "abc123",
            "title": "测试文章",
            "content": "中文内容" * 100,
            "summary": None,
            "pub_date": date(2026, 8, 5),
            "create_time": datetime(
                2026, 8, 5, 14, 44, 25
            ),
        }
    ]

    monkeypatch.setattr(
        reader, "_connect", lambda: connection
    )

    fields = [
        SimpleNamespace(
            name="id",
            is_primary_key=True,
        ),
        SimpleNamespace(
            name="content",
            is_primary_key=False,
        ),
    ]

    monkeypatch.setattr(
        "qa_eval.infrastructure.mysql.datasource_reader."
        "MySQLSchemaInspector.inspect",
        lambda self, database, table: fields,
    )

    result = reader.get_preview(
        limit=5,
        max_text_length=10,
    )

    assert len(result["rows"]) == 1

    row = result["rows"][0]

    assert row["title"] == "测试文章"
    assert row["summary"] is None
    assert row["pub_date"] == "2026-08-05"
    assert row["create_time"] == "2026-08-05T14:44:25"
    assert row["content"] == "中文内容" * 2 + "中文…"

    sql, params = cursor.execute.call_args.args

    assert "ORDER BY `id`" in sql
    assert params == (5,)

    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_preview_without_primary_key(
    reader, monkeypatch
):
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    connection.cursor.return_value = cursor

    monkeypatch.setattr(
        reader, "_connect", lambda: connection
    )

    fields = [
        SimpleNamespace(
            name="content",
            is_primary_key=False,
        )
    ]

    monkeypatch.setattr(
        "qa_eval.infrastructure.mysql.datasource_reader."
        "MySQLSchemaInspector.inspect",
        lambda self, database, table: fields,
    )

    reader.get_preview(limit=3)

    sql, params = cursor.execute.call_args.args

    assert "ORDER BY" not in sql
    assert params == (3,)

    cursor.close.assert_called_once()
    connection.close.assert_called_once()

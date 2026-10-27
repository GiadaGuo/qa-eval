
import pytest

from qa_eval.infrastructure.mysql.schema_inspector import (
    MySQLSchemaInspector,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None
        self.executed_params = None
        self.closed = False

    def execute(self, operation, params):
        self.executed_sql = operation
        self.executed_params = params

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows):
        self.fake_cursor = FakeCursor(rows)

    def cursor(self, dictionary=True):
        assert dictionary is True
        return self.fake_cursor


@pytest.fixture
def rows():
    return [
        {
            "name": "id",
            "data_type": "char",
            "is_nullable": "NO",
            "column_key": "PRI",
        },
        {
            "name": "title",
            "data_type": "varchar",
            "is_nullable": "YES",
            "column_key": "",
        },
        {
            "name": "content",
            "data_type": "longtext",
            "is_nullable": "YES",
            "column_key": "",
        },
        {
            "name": "create_time",
            "data_type": "datetime",
            "is_nullable": "YES",
            "column_key": "",
        },
    ]


def test_inspect_columns(rows):
    connection = FakeConnection(rows)

    fields = MySQLSchemaInspector(connection).inspect(
        "demo",
        "jhsjk_article",
    )

    assert len(fields) == 4
    assert [field.name for field in fields] == [
        "id",
        "title",
        "content",
        "create_time",
    ]


def test_primary_key_detection(rows):
    fields = MySQLSchemaInspector(
        FakeConnection(rows)
    ).inspect("demo", "jhsjk_article")

    assert fields[0].is_primary_key is True
    assert fields[1].is_primary_key is False
    assert fields[0].nullable is False


def test_query_uses_parameters(rows):
    connection = FakeConnection(rows)

    MySQLSchemaInspector(connection).inspect(
        "demo",
        "jhsjk_article",
    )

    cursor = connection.fake_cursor

    assert cursor.executed_params == (
        "demo",
        "jhsjk_article",
    )
    assert "ORDINAL_POSITION" in cursor.executed_sql


def test_cursor_closed(rows):
    connection = FakeConnection(rows)

    MySQLSchemaInspector(connection).inspect(
        "demo",
        "jhsjk_article",
    )

    assert connection.fake_cursor.closed is True


def test_empty_table_name_rejected(rows):
    inspector = MySQLSchemaInspector(
        FakeConnection(rows)
    )

    with pytest.raises(ValueError):
        inspector.inspect("demo", "")


def test_missing_table_rejected():
    inspector = MySQLSchemaInspector(
        FakeConnection([])
    )

    with pytest.raises(
        ValueError,
        match="Table not found",
    ):
        inspector.inspect("demo", "missing_table")

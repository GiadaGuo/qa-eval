# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 11:29:52
# @LastEditTime: 2026-10-21 11:31:12
# @Description: Inspector 接收数据库连接对象，而不是在内部创建连接
# ====================================================

from typing import Any, Protocol

from qa_eval.domain.entities.field_mapping import SourceField


class MySQLCursor(Protocol):
    def execute(
        self,
        operation: str,
        params: tuple[str, str],
    ) -> Any: ...

    def fetchall(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class MySQLConnection(Protocol):
    def cursor(
        self,
        dictionary: bool = True,
    ) -> MySQLCursor: ...


class MySQLSchemaInspector:
    """读取 MySQL 表结构，不读取业务数据。"""

    COLUMN_QUERY = """
        SELECT
            COLUMN_NAME AS name,
            DATA_TYPE AS data_type,
            IS_NULLABLE AS is_nullable,
            COLUMN_KEY AS column_key
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
    """

    def __init__(self, connection: MySQLConnection):
        self.connection = connection

    def inspect(
        self,
        database: str,
        table: str,
    ) -> list[SourceField]:
        if not database.strip() or not table.strip():
            raise ValueError(
                "Database and table must not be empty"
            )

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                self.COLUMN_QUERY,
                (database, table),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()

        if not rows:
            raise ValueError(
                f"Table not found or no columns visible: "
                f"{database}.{table}"
            )

        return [
            SourceField(
                name=row["name"],
                data_type=row["data_type"],
                nullable=(
                    row["is_nullable"] == "YES"
                ),
                is_primary_key=(
                    row["column_key"] == "PRI"
                ),
            )
            for row in rows
        ]

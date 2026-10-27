# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-22 02:05:40
# @LastEditTime: 2026-10-22 02:19:20
# @Description: 
# - Schema Discovery
# - 数据源记录数统计
# - 样本数据预览
# - 主键动态排序
# - 日期序列化、长文本截断、NULL 保留
# ====================================================

import re
from datetime import date, datetime
from typing import Any

import mysql.connector

from qa_eval.infrastructure.mysql.schema_inspector import (
    MySQLSchemaInspector,
)


class MySQLDataSourceReader:
    """MySQL 数据源只读访问层。"""

    _IDENTIFIER_PATTERN = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_]*$"
    )

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table: str,
    ):
        self.connection_config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
            "connection_timeout": 5,
        }

        self.database = database
        self.table = table

        # 初始化时校验 SQL 标识符
        self._quoted_table()

    def _connect(self):
        """创建 MySQL 连接。"""
        return mysql.connector.connect(
            **self.connection_config
        )

    def _quoted_table(self) -> str:
        """校验并安全引用数据库名和表名。"""

        if not self._IDENTIFIER_PATTERN.fullmatch(
            self.database
        ):
            raise ValueError(
                "Invalid database identifier"
            )

        if not self._IDENTIFIER_PATTERN.fullmatch(
            self.table
        ):
            raise ValueError(
                "Invalid table identifier"
            )

        return (
            f"`{self.database}`."
            f"`{self.table}`"
        )

    @staticmethod
    def _quote_column(name: str) -> str:
        """安全引用从数据库元数据获取的列名。"""
        return f"`{name.replace('`', '``')}`"

    def get_schema(self) -> dict[str, Any]:
        """获取表结构与记录数。"""

        connection = self._connect()

        try:
            fields = MySQLSchemaInspector(
                connection
            ).inspect(
                self.database,
                self.table,
            )

            table_sql = self._quoted_table()

            cursor = connection.cursor()

            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table_sql}"
                )

                row_count = cursor.fetchone()[0]

            finally:
                cursor.close()

            return {
                "database": self.database,
                "table": self.table,
                "row_count": row_count,
                "fields": [
                    {
                        "name": field.name,
                        "data_type": field.data_type,
                        "nullable": field.nullable,
                        "is_primary_key": field.is_primary_key,
                    }
                    for field in fields
                ],
            }

        finally:
            connection.close()

    def get_preview(
        self,
        limit: int = 5,
        max_text_length: int = 300,
    ) -> dict[str, Any]:
        """获取样本预览，保留 NULL 并截断长文本。"""

        if not 1 <= limit <= 20:
            raise ValueError(
                "limit must be between 1 and 20"
            )

        if not 1 <= max_text_length <= 2000:
            raise ValueError(
                "max_text_length must be between 1 and 2000"
            )

        connection = self._connect()

        try:
            table_sql = self._quoted_table()

            # 动态发现主键字段
            fields = MySQLSchemaInspector(
                connection
            ).inspect(
                self.database,
                self.table,
            )

            primary_keys = [
                field.name
                for field in fields
                if field.is_primary_key
            ]

            # 支持单主键、复合主键、无主键
            if primary_keys:
                order_columns = ", ".join(
                    self._quote_column(name)
                    for name in primary_keys
                )

                order_clause = (
                    f" ORDER BY {order_columns}"
                )
            else:
                order_clause = ""

            cursor = connection.cursor(
                dictionary=True
            )

            try:
                sql = (
                    f"SELECT * FROM {table_sql}"
                    f"{order_clause} LIMIT %s"
                )

                cursor.execute(
                    sql,
                    (limit,),
                )

                rows = cursor.fetchall()

            finally:
                cursor.close()

            preview_rows = []

            for row in rows:
                preview = {}

                for field, value in row.items():

                    # 1. NULL 原样保留
                    if value is None:
                        preview[field] = None

                    # 2. 日期转换为 ISO 字符串，不参与截断
                    elif isinstance(value, (date, datetime)):
                        preview[field] = value.isoformat()

                    # 3. 仅对原始文本字段执行截断
                    elif isinstance(value, str):
                        if len(value) > max_text_length:
                            preview[field] = (
                                value[:max_text_length] + "…"
                            )
                        else:
                            preview[field] = value

                    # 4. 数值等其他类型原样保留
                    else:
                        preview[field] = value


                preview_rows.append(preview)

            return {
                "database": self.database,
                "table": self.table,
                "limit": limit,
                "rows": preview_rows,
            }

        finally:
            connection.close()

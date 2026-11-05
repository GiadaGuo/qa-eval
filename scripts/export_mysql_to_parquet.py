"""把 MySQL 表导出为 Parquet（分批流式写入，不需要整表载入内存）。

用法：
    # 导出指定表（连接参数取 .env 中的 MYSQL_* 配置）
    uv run python scripts/export_mysql_to_parquet.py --table policy policy_documents

    # 指定 database / 输出目录 / 压缩算法
    uv run python scripts/export_mysql_to_parquet.py \
        --database policy --table policy --output-dir data/other --compression zstd
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error


def _arrow_value(value: object) -> object:
    """把 MySQL Python 值转换成 Arrow 友好的值。

    datetime/date/timedelta/bytes 由 Arrow 原生支持，直接透传；
    Decimal 统一转 float（保证各批次 schema 一致，牺牲超高精度）。
    """
    if isinstance(value, Decimal):
        return float(value)

    return value


def _infer_schema(rows: list[dict]) -> pa.Schema:
    """从首批行推断 Arrow schema；全空列统一按 string 处理，避免 null 类型。"""
    schema = pa.Table.from_pylist(rows).schema

    fields = [
        field.with_type(pa.string()) if pa.types.is_null(field.type) else field
        for field in schema
    ]
    return pa.schema(fields)


def export_table_to_parquet(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    table: str,
    output_path: Path,
    batch_size: int = 5000,
    compression: str = "zstd",
) -> int:
    """把整张 MySQL 表流式导出为 Parquet，返回写入行数。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    connection = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connection_timeout=5,
    )

    try:
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM `{database}`.`{table}`"
            )
            total = cursor.fetchone()["COUNT(*)"]
            print(f"Table `{database}`.`{table}` rows: {total}")

            cursor.execute(
                f"SELECT * FROM `{database}`.`{table}`"
            )

            # schema 依赖首批数据推断，所以 writer 延迟到首批行到达时才打开
            schema = None
            writer: pq.ParquetWriter | None = None
            written = 0

            try:
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break

                    rows = [
                        {k: _arrow_value(v) for k, v in row.items()}
                        for row in rows
                    ]

                    if writer is None:
                        schema = _infer_schema(rows)
                        writer = pq.ParquetWriter(
                            output_path,
                            schema,
                            compression=compression,
                        )

                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    written += len(rows)

                    print(
                        f"  exported {written}/{total} rows...",
                        end="\r",
                        flush=True,
                    )

                print()

            finally:
                if writer is not None:
                    writer.close()

            print(f"Parquet saved: {output_path} ({written} rows)")
            return written

        finally:
            cursor.close()

    finally:
        connection.close()


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="导出 MySQL 表为 Parquet"
    )
    parser.add_argument(
        "--table",
        nargs="+",
        required=True,
        help="要导出的表名，可一次指定多张",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("MYSQL_DATABASE", "policy"),
        help="数据库名（默认取 MYSQL_DATABASE 环境变量）",
    )
    parser.add_argument(
        "--output-dir",
        default="data/other",
        help="输出目录（默认 data/other）",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MYSQL_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MYSQL_PORT", "3307")),
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("MYSQL_USER", "root"),
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("MYSQL_PASSWORD", ""),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="每批读取并写入的行数，同时是一个 row group 的大小",
    )
    parser.add_argument(
        "--compression",
        default="zstd",
        choices=["zstd", "snappy", "gzip", "brotli", "none"],
        help="Parquet 压缩算法（默认 zstd，文本列压缩比高）",
    )
    args = parser.parse_args()

    try:
        for table in args.table:
            output_path = Path(args.output_dir) / f"{args.database}.{table}.parquet"
            export_table_to_parquet(
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                database=args.database,
                table=table,
                output_path=output_path,
                batch_size=args.batch_size,
                compression=args.compression,
            )

    except (Error, KeyError, ValueError, pa.ArrowException) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""把 MySQL 表导出为 JSONL（每行一条 JSON 记录）。

用法：
    # 导出指定表（连接参数取 .env 中的 MYSQL_* 配置）
    uv run python scripts/export_mysql_to_jsonl.py --table policy policy_documents

    # 指定 database / 输出目录
    uv run python scripts/export_mysql_to_jsonl.py \
        --database policy --table policy --output-dir data/other
"""

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error


def _json_value(value: object) -> object:
    """把 MySQL Python 值转换成 JSON 可序列化的值。"""
    if value is None:
        return None

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


def export_table_to_jsonl(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    table: str,
    output_path: Path,
    batch_size: int = 1000,
) -> int:
    """把整张 MySQL 表导出为 UTF-8 JSONL，返回写入行数。"""

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

            with output_path.open("w", encoding="utf-8") as f:
                written = 0

                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break

                    for row in rows:
                        f.write(
                            json.dumps(
                                {k: _json_value(v) for k, v in row.items()},
                                ensure_ascii=False,
                                default=str,
                            )
                        )
                        f.write("\n")
                        written += 1

                    print(
                        f"  exported {written}/{total} rows...",
                        end="\r",
                        flush=True,
                    )

                print()

            print(f"JSONL saved: {output_path}")
            return written

        finally:
            cursor.close()

    finally:
        connection.close()


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="导出 MySQL 表为 JSONL"
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
    args = parser.parse_args()

    try:
        for table in args.table:
            output_path = Path(args.output_dir) / f"{args.database}.{table}.jsonl"
            export_table_to_jsonl(
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                database=args.database,
                table=table,
                output_path=output_path,
            )

    except (Error, KeyError, ValueError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

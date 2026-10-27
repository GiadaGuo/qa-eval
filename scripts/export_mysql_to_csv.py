import csv
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error


def _json_value(value: object) -> object:
    """把 MySQL Python 值转换成 CSV 可打印的标量。"""
    if value is None:
        return None

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


def export_table_to_csv(
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
    """把整张 MySQL 表导出为 UTF-8 CSV。"""

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

            with output_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as f:
                writer = None
                written = 0

                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break

                    if writer is None:
                        writer = csv.DictWriter(
                            f,
                            fieldnames=rows[0].keys(),
                            extrasaction="ignore",
                        )
                        writer.writeheader()

                    for row in rows:
                        writer.writerow(
                            {
                                k: _json_value(v)
                                for k, v in row.items()
                            }
                        )
                        written += 1

                    print(
                        f"  exported {written}/{total} rows...",
                        end="\r",
                        flush=True,
                    )

                print()

            print(f"CSV saved: {output_path}")
            return 0

        finally:
            cursor.close()

    finally:
        connection.close()


def main() -> int:
    load_dotenv()

    try:
        database = os.environ["MYSQL_DATABASE"]
        table = os.environ.get("MYSQL_TABLE", "jhsjk_article")

        output_path = Path(
            os.environ.get(
                "EXPORT_OUTPUT_PATH",
                f"data/other/{table}.csv",
            )
        )

        return export_table_to_csv(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3307")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=database,
            table=table,
            output_path=output_path,
        )

    except (Error, KeyError, ValueError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

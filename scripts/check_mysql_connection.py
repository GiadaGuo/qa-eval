
import os
import sys

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error

from qa_eval.infrastructure.mysql.schema_inspector import (
    MySQLSchemaInspector,
)


def main() -> int:
    load_dotenv()

    connection = None

    try:
        database = os.environ["MYSQL_DATABASE"]
        table = os.environ.get(
            "MYSQL_TABLE",
            "jhsjk_article",
        )

        connection = mysql.connector.connect(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3307")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=database,
            connection_timeout=5,
            charset="utf8mb4",
        )

        print("MySQL connection: OK")
        print(f"Database: {database}")
        print(f"Table: {table}")

        inspector = MySQLSchemaInspector(connection)
        fields = inspector.inspect(database, table)

        print(f"Columns discovered: {len(fields)}")
        print()

        for field in fields:
            print(
                f"{field.name:<22}"
                f"{field.data_type:<14}"
                f"nullable={field.nullable!s:<6}"
                f"primary_key={field.is_primary_key}"
            )

        if len(fields) != 10:
            print(
                "Warning: expected 10 columns "
                "in the local demo table."
            )

        print("\nSchema discovery: OK")
        return 0

    except (Error, ValueError, KeyError) as exc:
        print(
            f"MySQL verification failed: {exc}",
            file=sys.stderr,
        )
        return 1

    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())


import os
from uuid import uuid4

import mysql.connector
from dotenv import load_dotenv

from qa_eval.domain.entities.field_mapping import FieldMappingConfig
from qa_eval.domain.ports.dataset_adapter import AdapterContext
from qa_eval.infrastructure.mysql.record_adapter import MySQLRecordAdapter
from qa_eval.infrastructure.mysql.schema_inspector import MySQLSchemaInspector


def main():
    load_dotenv()

    database = os.environ["MYSQL_DATABASE"]
    table = os.environ.get("MYSQL_TABLE", "jhsjk_article")

    # 此脚本仅验证固定的本地模拟表
    if (database, table) != ("qa_eval_demo", "jhsjk_article"):
        raise ValueError(
            "This verification script only supports "
            "qa_eval_demo.jhsjk_article"
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

    try:
        # 1. 自动发现字段
        source_fields = MySQLSchemaInspector(
            connection
        ).inspect(database, table)

        # 2. 用户字段分类方案（暂时模拟前端提交）
        config = FieldMappingConfig(
            sample_id_field="id",
            core_fields=[
                "content",
                "plain_text_content",
            ],
            metadata_fields=[
                "article_id",
                "title",
                "url",
                "pub_date",
                "origin_name",
                "summary",
            ],
            time_fields=["create_time"],
        )

        resolved = config.resolve(source_fields)

        print(f"Discovered columns: {len(source_fields)}")
        print(f"Other fields: {resolved.other_fields}")

        # 3. 读取本地模拟数据
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT *
                FROM jhsjk_article
                ORDER BY create_time, id
                LIMIT 100
                """
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()

        # 4. 统一转换
        adapter = MySQLRecordAdapter(resolved)

        context = AdapterContext(
            dataset_version_id=uuid4(),
            source_uri="mysql://qa_eval_demo/jhsjk_article",
            input_fingerprint="demo-verification",
        )

        records = [
            adapter.adapt(row, context)
            for row in rows
        ]

        # 5. 验证转换结果
        assert len(records) == 20
        assert all(
            record.schema_version == "1.1"
            for record in records
        )
        assert all(
            len(record.data_content) == 2
            for record in records
        )

        # 日期应正确转换为 JSON 字符串
        assert all(
            record.metadata["pub_date"] is None
            or isinstance(
                record.metadata["pub_date"], str
            )
            for record in records
        )

        # 所有记录均能序列化
        for record in records:
            record.model_dump_json()

        print(f"Rows fetched: {len(rows)}")
        print(f"SampleRecords created: {len(records)}")

        # 输出简短样本信息，避免打印完整文章正文
        first = records[0]

        print("\nFirst SampleRecord:")
        print(f"  sample_id: {first.sample_id}")
        print(f"  title: {first.metadata.get('title')}")
        print(f"  pub_date: {first.metadata.get('pub_date')}")
        print(f"  create_time: {first.timestamps.get('create_time')}")
        print(f"  content_items: {len(first.data_content)}")

        print("\nMySQL -> SampleRecord: OK")

    finally:
        connection.close()


if __name__ == "__main__":
    main()


from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qa_eval.domain.entities.field_mapping import (
    FieldMappingConfig,
    SourceField,
)
from qa_eval.domain.ports.dataset_adapter import AdapterContext
from qa_eval.infrastructure.mysql.record_adapter import MySQLRecordAdapter


@pytest.fixture
def raw_record():
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "article_id": "A1001",
        "title": "测试文章",
        "url": "https://example.com/article/1",
        "origin_name": "测试来源",
        "summary": "测试摘要",
        "pub_date": "2026-10-20",
        "content": "<p>文章正文</p>",
        "plain_text_content": "文章正文",
        "create_time": datetime(2026, 10, 20, 10, 0, 0),
        "extra_score": Decimal("99.95"),
    }


@pytest.fixture
def mapping(raw_record):
    source_fields = [
        SourceField(name=name, data_type="varchar")
        for name in raw_record
    ]

    config = FieldMappingConfig(
        sample_id_field="id",
        core_fields=["content", "plain_text_content"],
        metadata_fields=[
            "article_id",
            "title",
            "url",
            "origin_name",
            "summary",
            "pub_date",
        ],
        time_fields=["create_time"],
    )

    return config.resolve(source_fields)


@pytest.fixture
def context():
    return AdapterContext(
        dataset_version_id=uuid4(),
        source_uri="mysql://demo/jhsjk_article",
        input_fingerprint="sha256:test-snapshot",
    )


def test_article_mapping(raw_record, mapping, context):
    sample = MySQLRecordAdapter(mapping).adapt(
        raw_record, context
    )

    assert sample.schema_version == "1.1"
    assert sample.sample_id == raw_record["id"]

    assert len(sample.data_content) == 2
    assert sample.get_content_by_role("content")[0].content == (
        "<p>文章正文</p>"
    )
    assert sample.metadata["title"] == "测试文章"
    assert sample.timestamps["create_time"] == (
        "2026-10-20T10:00:00"
    )
    assert sample.other_fields["extra_score"] == "99.95"


def test_missing_source_field_rejected(
    raw_record, mapping, context
):
    raw_record.pop("content")

    with pytest.raises(
        ValueError,
        match="Missing source fields",
    ):
        MySQLRecordAdapter(mapping).adapt(
            raw_record, context
        )


def test_null_core_content_allowed(
    raw_record, mapping, context
):
    raw_record["content"] = None

    sample = MySQLRecordAdapter(mapping).adapt(
        raw_record, context
    )

    assert sample.get_content_by_role("content")[0].content == ""


def test_invalid_timestamp_rejected(
    raw_record, mapping, context
):
    raw_record["create_time"] = "not-a-date"

    with pytest.raises(ValueError, match="Invalid timestamp"):
        MySQLRecordAdapter(mapping).adapt(
            raw_record, context
        )


def test_decimal_preserves_precision(
    raw_record, mapping, context
):
    sample = MySQLRecordAdapter(mapping).adapt(
        raw_record, context
    )

    assert sample.other_fields["extra_score"] == "99.95"


def test_json_roundtrip(raw_record, mapping, context):
    sample = MySQLRecordAdapter(mapping).adapt(
        raw_record, context
    )

    restored = type(sample).model_validate_json(
        sample.model_dump_json()
    )

    assert restored == sample


def test_same_input_same_output(raw_record, mapping, context):
    adapter = MySQLRecordAdapter(mapping)

    first = adapter.adapt(raw_record, context)
    second = adapter.adapt(raw_record, context)

    assert first.model_dump() == second.model_dump()


def test_original_record_not_modified(
    raw_record, mapping, context
):
    original = raw_record.copy()

    MySQLRecordAdapter(mapping).adapt(
        raw_record, context
    )

    assert raw_record == original

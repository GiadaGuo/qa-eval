
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qa_eval.domain.entities.sample_record import (
    ContentItem,
    SampleLineage,
    SampleRecord,
    SampleSource,
)


def make_sample():
    return SampleRecord(
        dataset_version_id=uuid4(),
        sample_id="article-uuid-001",
        rid=["article-uuid-001"],
        data_content=[
            ContentItem(
                content_id="title",
                media_type="text",
                role="title",
                content="测试文章标题",
            ),
            ContentItem(
                content_id="body",
                media_type="text",
                role="body_text",
                content="这是一篇测试文章的正文。",
            ),
        ],
        source=SampleSource(
            source_type="mysql",
            source_uri="mysql://demo/jhsjk_article",
            source_record_id="article-uuid-001",
        ),
        lineage=SampleLineage(
            adapter_id="mysql_article",
            adapter_version="1.0.0",
        ),
    )


def test_sample_record_creation():
    sample = make_sample()

    # 显式指定旧版本，验证向后兼容
    payload = sample.model_dump()
    payload["schema_version"] = "1.0"
    sample_v10 = SampleRecord.model_validate(payload)

    assert sample_v10.schema_version == "1.0"
    assert len(sample_v10.data_content) == 2
    assert sample_v10.sample_id == "article-uuid-001"

def test_sample_record_default_version():
    sample = make_sample()

    assert sample.schema_version == "1.1"


def test_get_content_by_role():
    sample = make_sample()

    body = sample.get_content_by_role("body_text")

    assert len(body) == 1
    assert body[0].content == "这是一篇测试文章的正文。"


def test_duplicate_content_id_rejected():
    sample = make_sample()
    payload = sample.model_dump()

    payload["data_content"][1]["content_id"] = "title"

    with pytest.raises(ValidationError):
        SampleRecord.model_validate(payload)


def test_content_requires_payload():
    with pytest.raises(ValidationError):
        ContentItem(
            content_id="empty",
            media_type="text",
            role="body_text",
        )


def test_json_roundtrip():
    sample = make_sample()

    restored = SampleRecord.model_validate_json(
        sample.model_dump_json()
    )

    assert restored == sample

def test_sample_record_v11_groups():
    """
    四类字段可以独立保存
    """
    sample = make_sample()

    payload = sample.model_dump()
    payload["schema_version"] = "1.1"
    payload["metadata"] = {
        "title": "测试文章",
        "article_id": "A1001",
    }
    payload["timestamps"] = {
        "create_time": "2026-10-20T10:00:00Z"
    }
    payload["other_fields"] = {
        "extra_column": "extra_value",
        "nullable_column": None,
    }

    result = SampleRecord.model_validate(payload)

    assert result.schema_version == "1.1"
    assert result.metadata["article_id"] == "A1001"
    assert result.timestamps["create_time"] == (
        "2026-10-20T10:00:00Z"
    )
    assert result.other_fields["extra_column"] == (
        "extra_value"
    )


def test_sample_record_v11_json_roundtrip():
    """
    新增字段能够正常 JSON 序列化与反序列化
    """
    sample = make_sample()

    payload = sample.model_dump()
    payload["schema_version"] = "1.1"
    payload["timestamps"] = {
        "create_time": "2026-10-20T10:00:00Z"
    }
    payload["other_fields"] = {
        "extra_count": 12,
        "extra_flag": True,
    }

    record = SampleRecord.model_validate(payload)
    restored = SampleRecord.model_validate_json(
        record.model_dump_json()
    )

    assert restored == record


def test_sample_record_v10_compatibility():
    """
    旧版 1.0 样本仍能被读取
    """
    sample = make_sample()

    payload = sample.model_dump()
    payload["schema_version"] = "1.0"
    payload.pop("timestamps", None)
    payload.pop("other_fields", None)

    restored = SampleRecord.model_validate(payload)

    assert restored.schema_version == "1.0"
    assert restored.timestamps == {}
    assert restored.other_fields == {}

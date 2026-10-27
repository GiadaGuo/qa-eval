# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 11:21:37
# @LastEditTime: 2026-10-21 11:22:06
# @Description: 
# ====================================================

import pytest
from pydantic import ValidationError

from qa_eval.domain.entities.field_mapping import (
    FieldMappingConfig,
    SourceField,
)


@pytest.fixture
def source_fields():
    names = [
        "id",
        "article_id",
        "title",
        "url",
        "pub_date",
        "origin_name",
        "summary",
        "content",
        "plain_text_content",
        "create_time",
        "extra_column",
    ]

    return [
        SourceField(
            name=name,
            data_type="varchar",
            is_primary_key=(name == "id"),
        )
        for name in names
    ]


@pytest.fixture
def mapping():
    return FieldMappingConfig(
        sample_id_field="id",
        core_fields=[
            "content",
            "plain_text_content",
        ],
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


def test_valid_mapping(mapping):
    assert mapping.sample_id_field == "id"
    assert len(mapping.core_fields) == 2


def test_resolve_other_fields(mapping, source_fields):
    resolved = mapping.resolve(source_fields)

    assert resolved.other_fields == ["extra_column"]


def test_all_fields_classified(mapping, source_fields):
    resolved = mapping.resolve(source_fields)

    groups = [
        [resolved.sample_id_field],
        resolved.core_fields,
        resolved.metadata_fields,
        resolved.time_fields,
        resolved.other_fields,
    ]

    assigned = [
        name
        for group in groups
        for name in group
    ]

    assert set(assigned) == {
        field.name
        for field in source_fields
    }
    assert len(assigned) == len(set(assigned))


def test_duplicate_field_rejected():
    with pytest.raises(ValidationError):
        FieldMappingConfig(
            sample_id_field="id",
            core_fields=["content"],
            metadata_fields=["content"],
        )


def test_identity_field_cannot_be_reused():
    with pytest.raises(ValidationError):
        FieldMappingConfig(
            sample_id_field="id",
            core_fields=["id"],
        )


def test_unknown_field_rejected(mapping, source_fields):
    mapping.metadata_fields.append("not_exists")

    with pytest.raises(
        ValueError,
        match="Unknown source fields",
    ):
        mapping.resolve(source_fields)


def test_core_fields_required():
    with pytest.raises(ValidationError):
        FieldMappingConfig(
            sample_id_field="id",
            core_fields=[],
        )


def test_duplicate_source_columns_rejected(
    mapping, source_fields
):
    source_fields.append(
        SourceField(
            name="id",
            data_type="varchar",
        )
    )

    with pytest.raises(
        ValueError,
        match="Duplicate field names",
    ):
        mapping.resolve(source_fields)


def test_json_roundtrip(mapping):
    restored = FieldMappingConfig.model_validate_json(
        mapping.model_dump_json()
    )

    assert restored == mapping

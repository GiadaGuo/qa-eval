from qa_eval.contracts.datasets import FieldMappingSpecV2


def test_structured_record_mapping_contract_accepts_record_target():
    mapping = FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {
                "key": "tobix.sql.structured_snapshot",
                "version": "1.0",
                "parameters": {
                    "schema_fingerprint": "sha256:" + "0" * 64,
                    "physical_schema": [{"name": "id", "type": "BIGINT UNSIGNED"}],
                },
            },
            "mappings": [
                {
                    "source_path": "$.sample_id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID", "name": "sample_id"},
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.fields",
                    "action": "MAP",
                    "target": {
                        "kind": "STRUCTURED_RECORD",
                        "name": "record",
                        "role": "structured.record",
                        "modality": "structured",
                    },
                    "presence_policy": "PRESERVE",
                },
            ],
        }
    )

    assert mapping.mappings[1].target is not None
    assert mapping.mappings[1].target.kind == "STRUCTURED_RECORD"
    assert mapping.adapter.parameters["physical_schema"][0]["name"] == "id"


# ---------------------------------------------------------------------------
# TOBIX 结构化快照端到端保真：upload → STRUCTURED_RECORD mapping → publish →
# 从不可变 NORMALIZED_DATA Artifact 按 sample_id 找回并逐字段比对。
# 覆盖：NULL / 空字符串 / LONGTEXT / DECIMAL(字符串承载) / BIGINT UNSIGNED /
#       中文字符 / 特殊字符 / 布尔差异。
# ---------------------------------------------------------------------------

import json

import pytest

from qa_eval.application.artifact_jsonl import find_jsonl_record, read_jsonl_page
from qa_eval.application.services.dataset_version_service import DatasetVersionService
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

from test_dataset_version_service import (
    FakeDatasetProfileRepository,
    FakeDatasetRepository,
    FakeDatasetVersionRepository,
)


@pytest.fixture
def structured_service(tmp_path):
    return DatasetVersionService(
        dataset_repository=FakeDatasetRepository(),
        version_repository=FakeDatasetVersionRepository(),
        profile_repository=FakeDatasetProfileRepository(),
        artifact_store=LocalFileSystemArtifactStore(tmp_path),
    )


def _tobix_structured_mapping(fingerprint: str) -> FieldMappingSpecV2:
    """与 TOBIX QaEvaluationClient.saveStructuredFieldMapping 完全一致的 wire 结构。"""
    return FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {
                "key": "tobix.sql.structured_snapshot",
                "version": "1.0",
                "parameters": {
                    "schema_fingerprint": fingerprint,
                    "physical_schema": [
                        {"name": "id", "type": "BIGINT UNSIGNED"},
                        {"name": "product_code", "type": "VARCHAR"},
                        {"name": "product_name", "type": "VARCHAR"},
                        {"name": "wholesale_price", "type": "DECIMAL"},
                        {"name": "spec_text", "type": "LONGTEXT"},
                        {"name": "remark", "type": "VARCHAR"},
                        {"name": "active", "type": "TINYINT"},
                    ],
                },
            },
            "mappings": [
                {
                    "source_path": "$.sample_id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID", "name": "sample_id"},
                    "presence_policy": "REJECT_MISSING",
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.fields",
                    "action": "MAP",
                    "target": {
                        "kind": "STRUCTURED_RECORD",
                        "name": "record",
                        "role": "record",
                        "modality": "structured",
                    },
                    "presence_policy": "PRESERVE",
                },
            ],
        }
    )


_ROW_1_FIELDS = {
    "id": 1,
    "product_code": "P001",
    "product_name": "中华（软）卷烟",
    "wholesale_price": "120.50",
    "spec_text": "规格说明文本，包含制表符\t、引号\"、尖括号<>与反斜杠\\。" * 12,
    "remark": "",
    "active": True,
}

_ROW_2_FIELDS = {
    "id": 2,
    "product_code": "P002",
    "product_name": None,
    "wholesale_price": None,
    # BIGINT UNSIGNED 上界，JSON integer 精确承载
    "spec_text": None,
    "remark": None,
    "active": False,
}
_ROW_2_FIELDS["id"] = 18446744073709551615


def test_tobix_structured_snapshot_roundtrip_preserves_field_fidelity(
    structured_service: DatasetVersionService,
):
    dataset = structured_service._dataset_repository.add(
        Dataset(name="cigarette_products", modality=["structured"])
    )
    version = structured_service.create_version(dataset.id, "v1")

    raw_lines = [
        {"sample_id": "cigarette_products:1", "fields": _ROW_1_FIELDS},
        {"sample_id": "cigarette_products:2", "fields": _ROW_2_FIELDS},
    ]
    raw = (
        "\n".join(json.dumps(line, ensure_ascii=False) for line in raw_lines)
        + "\n"
    ).encode("utf-8")
    upload = structured_service.upload_raw_file(version.id, "snapshot.jsonl", raw)
    assert upload["sha256"]

    structured_service.save_field_mapping(
        version.id, _tobix_structured_mapping("sha256:" + "1" * 64)
    )

    published = structured_service.publish_version(version.id)
    assert published["status"] == "PUBLISHED"
    assert published["counts"]["input"] == 2
    assert published["counts"]["normalized"] == 2
    assert published["counts"]["rejected"] == 0

    manifest = structured_service.get_manifest(version.id)
    normalized = next(a for a in manifest.artifacts if a.kind == "NORMALIZED_DATA")
    assert normalized.record_count == 2

    store = structured_service._artifact_store
    page = read_jsonl_page(store, normalized.uri, offset=0, limit=10)
    assert len(page) == 2

    found = find_jsonl_record(
        store, normalized.uri, lambda record: record["sample_id"] == "cigarette_products:2"
    )
    assert found is not None
    structured_items = [
        item
        for item in found["contents"]
        if item.get("modality") == "structured"
        and item.get("payload", {}).get("type") == "structured"
    ]
    assert len(structured_items) == 1
    assert structured_items[0]["presence"] == "PRESENT"

    data = structured_items[0]["payload"]["data"]
    # 逐字段保真断言：NULL、空串、布尔、BIGINT UNSIGNED、DECIMAL 字符串、中文与特殊字符
    assert data["id"] == 18446744073709551615
    assert data["product_name"] is None
    assert data["wholesale_price"] is None
    assert data["spec_text"] is None
    assert data["active"] is False

    first = find_jsonl_record(
        store, normalized.uri, lambda record: record["sample_id"] == "cigarette_products:1"
    )
    first_data = next(
        item["payload"]["data"]
        for item in first["contents"]
        if item.get("payload", {}).get("type") == "structured"
    )
    assert first_data["product_name"] == "中华（软）卷烟"
    assert first_data["wholesale_price"] == "120.50"
    assert first_data["remark"] == ""
    assert first_data["active"] is True
    assert first_data["spec_text"] == _ROW_1_FIELDS["spec_text"]


def test_structured_dataset_does_not_require_content_mapping(
    structured_service: DatasetVersionService,
):
    """结构化数据集不强制提供文本 CONTENT：仅 SAMPLE_ID + STRUCTURED_RECORD 即可发布。"""
    dataset = structured_service._dataset_repository.add(
        Dataset(name="sql_table_mirror", modality=["structured"])
    )
    version = structured_service.create_version(dataset.id, "v1")
    raw = json.dumps(
        {"sample_id": "t:1", "fields": {"a": 1, "b": None}}, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    structured_service.upload_raw_file(version.id, "snapshot.jsonl", raw)
    structured_service.save_field_mapping(
        version.id, _tobix_structured_mapping("sha256:" + "2" * 64)
    )
    published = structured_service.publish_version(version.id)
    assert published["status"] == "PUBLISHED"
    assert published["counts"]["rejected"] == 0

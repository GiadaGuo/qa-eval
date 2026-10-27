# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description: DatasetVersionService 单元测试（使用 fake repositories）。
# ====================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from qa_eval.application.services.dataset_version_service import (
    DatasetVersionService,
)
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.entities.dataset_version import (
    DatasetVersion,
    DatasetVersionStatus,
)
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.domain.repositories.dataset_profile_repository import (
    DatasetProfileRepository,
)
from qa_eval.domain.repositories.dataset_repository import DatasetRepository
from qa_eval.domain.repositories.dataset_version_repository import (
    DatasetVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


class FakeDatasetRepository(DatasetRepository):
    def __init__(self) -> None:
        self._items: dict[UUID, Dataset] = {}

    def add(self, dataset: Dataset) -> Dataset:
        self._items[dataset.id] = dataset
        return dataset

    def get_by_id(self, dataset_id: UUID) -> Dataset | None:
        return self._items.get(dataset_id)

    def list_all(self) -> list[Dataset]:
        return list(self._items.values())

    def delete(self, dataset_id: UUID) -> bool:
        if dataset_id not in self._items:
            return False
        del self._items[dataset_id]
        return True


class FakeDatasetVersionRepository(DatasetVersionRepository):
    def __init__(self) -> None:
        self._items: dict[UUID, DatasetVersion] = {}
        self._refs: dict[UUID, dict] = {}
        self._dependencies: dict[UUID, dict[str, int]] = {}

    def add(self, version: DatasetVersion) -> DatasetVersion:
        self._items[version.id] = version
        return version

    def get_by_id(self, version_id: UUID) -> DatasetVersion | None:
        return self._items.get(version_id)

    def list_by_dataset(self, dataset_id: UUID) -> list[DatasetVersion]:
        return [v for v in self._items.values() if v.dataset_id == dataset_id]

    def update(self, version: DatasetVersion) -> DatasetVersion:
        self._items[version.id] = version
        return version

    def update_artifact_refs(
        self,
        version_id: UUID,
        artifact_refs: dict,
        content_fingerprint: str | None = None,
    ) -> DatasetVersion:
        self._refs[version_id] = artifact_refs
        return self._items[version_id]

    def get_artifact_refs(self, version_id: UUID) -> dict | None:
        return self._refs.get(version_id)

    def count_blocking_dependencies(
        self, version_id: UUID
    ) -> dict[str, int]:
        return self._dependencies.get(version_id, {})

    def delete(self, version_id: UUID) -> bool:
        if version_id not in self._items:
            return False
        del self._items[version_id]
        self._refs.pop(version_id, None)
        return True


class FakeDatasetProfileRepository(DatasetProfileRepository):
    def __init__(self) -> None:
        self._items: dict[UUID, Any] = {}

    def add(self, profile, checksum_sha256: str):
        self._items[profile.dataset_version_id] = (profile, checksum_sha256)
        return profile

    def get_by_dataset_version(self, dataset_version_id: UUID):
        item = self._items.get(dataset_version_id)
        return item[0] if item else None

    def list_by_dataset_version(self, dataset_version_id: UUID):
        item = self._items.get(dataset_version_id)
        return [item[0]] if item else []


@pytest.fixture
def service(tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path)
    return DatasetVersionService(
        dataset_repository=FakeDatasetRepository(),
        version_repository=FakeDatasetVersionRepository(),
        profile_repository=FakeDatasetProfileRepository(),
        artifact_store=store,
    )


def _legacy_record(sample_id: str, text: str | None) -> str:
    return json.dumps(
        {"sample_id": sample_id, "text": text, "text_is_null": text is None},
        ensure_ascii=False,
    )


def _mapping() -> FieldMappingSpecV2:
    return FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
            "mappings": [
                {
                    "source_path": "$.sample_id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID"},
                    "value_type": "string",
                    "presence_policy": "REJECT_MISSING",
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.text",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.plain",
                        "modality": "text",
                    },
                    "value_type": "string|null",
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
            ],
        }
    )


def test_create_version_requires_existing_dataset(service: DatasetVersionService):
    with pytest.raises(ValueError, match="Dataset not found"):
        service.create_version(uuid4(), "v1")


def test_full_upload_and_publish_closes_counts(service: DatasetVersionService):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )

    version = service.create_version(dataset.id, "v1")
    assert version.status == DatasetVersionStatus.DRAFT

    raw_content = (
        _legacy_record("a", "hello world")
        + "\n"
        + _legacy_record("b", None)
        + "\n"
        + _legacy_record("c", "another text")
        + "\n"
    ).encode("utf-8")

    upload_result = service.upload_raw_file(version.id, "raw.jsonl", raw_content)
    assert upload_result["sha256"] is not None
    inspection = service.inspect_uploaded_source(version.id, preview_limit=2)
    assert inspection["record_count"] == 3
    assert inspection["filename"] == "raw.jsonl"
    assert [field["name"] for field in inspection["fields"]] == [
        "sample_id",
        "text",
        "text_is_null",
    ]
    assert inspection["fields"][1]["data_type"] == "null|string"
    assert inspection["fields"][1]["nullable"] is True
    assert len(inspection["rows"]) == 2

    service.save_field_mapping(version.id, _mapping())

    publish_result = service.publish_version(version.id)
    assert publish_result["status"] == "PUBLISHED"
    assert publish_result["counts"]["input"] == 3
    assert publish_result["counts"]["normalized"] == 3
    assert publish_result["counts"]["rejected"] == 0
    assert publish_result["profile"]["identity"]["sample_count"] == 3
    assert publish_result["profile"]["structure"]["content_roles"] == [
        "article.body.plain"
    ]
    assert publish_result["profile"]["statistics"]["text_length"]["p50"] == 11

    manifest = service.get_manifest(version.id)
    assert manifest.schema_version == "2.0"
    assert manifest.dataset_version_id == version.id

    profile = service.get_profile(version.id)
    assert profile.summary.source_record_count == 3
    assert profile.summary.normalized_record_count == 3
    assert profile.summary.rejected_record_count == 0
    assert profile.profiler.key == "base_dataset_profiler"


def test_full_field_mapping_generates_complete_article_sample(
    service: DatasetVersionService,
):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )
    version = service.create_version(dataset.id, "full-v1")
    mapping = FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
            "mappings": [
                {
                    "source_path": "$.id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID"},
                    "presence_policy": "REJECT_MISSING",
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.title",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.title",
                        "modality": "text",
                    },
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
                {
                    "source_path": "$.content",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.html",
                        "modality": "text",
                    },
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
                {
                    "source_path": "$.plain_text_content",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.plain",
                        "modality": "text",
                    },
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
                {
                    "source_path": "$.article_id",
                    "action": "MAP",
                    "target": {"kind": "METADATA", "name": "article_id"},
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
                {
                    "source_path": "$.url",
                    "action": "MAP",
                    "target": {"kind": "SOURCE_ATTRIBUTE", "name": "url"},
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
                {
                    "source_path": "$.pub_date",
                    "action": "MAP",
                    "target": {"kind": "TEMPORAL", "name": "published_at"},
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
            ],
        }
    )
    raw = json.dumps(
        {
            "id": "1",
            "title": "标题",
            "content": "<p>正文</p>",
            "plain_text_content": "正文",
            "article_id": 42,
            "url": "https://example.test/1",
            "pub_date": "2026-10-01 10:00:00",
        },
        ensure_ascii=False,
    ).encode()
    service.upload_raw_file(version.id, "article.jsonl", raw + b"\n")
    service.save_field_mapping(version.id, mapping)
    result = service.publish_version(version.id)

    assert result["counts"] == {"input": 1, "normalized": 1, "rejected": 0}
    manifest = service.get_manifest(version.id)
    normalized = service._artifact_store.read(
        next(a.uri for a in manifest.artifacts if a.kind == "NORMALIZED_DATA")
    )
    sample = json.loads(normalized)
    assert {item["role"] for item in sample["contents"]} == {
        "article.title",
        "article.body.html",
        "article.body.plain",
    }
    assert sample["metadata"]["article_id"] == 42
    assert sample["metadata"]["published_at"] == {
        "value": "2026-10-01 10:00:00",
        "precision": "SECOND",
        "source_timezone": None,
        "timezone_status": "UNKNOWN",
    }
    assert sample["source"]["attributes"]["url"] == "https://example.test/1"


def test_publish_rejects_missing_raw_file(service: DatasetVersionService):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )
    version = service.create_version(dataset.id, "v1")

    with pytest.raises(ValueError, match="No raw file uploaded"):
        service.publish_version(version.id)


def test_mapping_requires_upload_and_reupload_invalidates_mapping(
    service: DatasetVersionService,
):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )
    version = service.create_version(dataset.id, "v1")

    with pytest.raises(ValueError, match="Upload a raw JSONL"):
        service.save_field_mapping(version.id, _mapping())

    content = (_legacy_record("a", "first") + "\n").encode()
    service.upload_raw_file(version.id, "first.jsonl", content)
    service.save_field_mapping(version.id, _mapping())
    assert service.get_setup_status(version.id)["has_field_mapping"] is True

    service.upload_raw_file(version.id, "second.jsonl", content)
    assert service.get_setup_status(version.id) == {
        "has_raw_file": True,
        "has_field_mapping": False,
    }


def test_upload_rejects_unrecognized_jsonl_before_storing(
    service: DatasetVersionService,
):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )
    version = service.create_version(dataset.id, "v1")

    with pytest.raises(TypeError, match="must be a JSON object"):
        service.upload_raw_file(
            version.id,
            "raw.jsonl",
            b'["not", "an", "object"]\n',
        )

    assert service._version_repository.get_artifact_refs(version.id) is None


def _make_version(service: DatasetVersionService, label: str = "v1"):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )
    version = service.create_version(dataset.id, label)
    return dataset, version


def test_delete_version_removes_version_without_dependencies(
    service: DatasetVersionService,
):
    dataset, version = _make_version(service)

    assert service.delete_version(dataset.id, version.id) is True
    assert service._version_repository.get_by_id(version.id) is None


def test_delete_missing_version_returns_false(service: DatasetVersionService):
    dataset = service._dataset_repository.add(
        Dataset(name="policy.jhsjk_article", modality=["text"])
    )

    assert service.delete_version(dataset.id, uuid4()) is False


def test_delete_version_of_other_dataset_raises_value_error(
    service: DatasetVersionService,
):
    _dataset, version = _make_version(service)
    other = service._dataset_repository.add(
        Dataset(name="other", modality=["text"])
    )

    with pytest.raises(ValueError, match="does not belong to dataset"):
        service.delete_version(other.id, version.id)

    assert service._version_repository.get_by_id(version.id) is not None


def test_delete_version_blocked_by_evaluation_dependency(
    service: DatasetVersionService,
):
    dataset, version = _make_version(service)
    service._version_repository._dependencies[version.id] = {
        "evaluation_runs": 2,
        "quality_reports": 1,
    }

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        service.delete_version(dataset.id, version.id)

    assert exc_info.value.code == "DATASET_VERSION_IN_USE"
    assert exc_info.value.dependencies == {
        "evaluation_runs": 2,
        "quality_reports": 1,
    }
    assert service._version_repository.get_by_id(version.id) is not None

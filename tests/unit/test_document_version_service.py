# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description: DocumentVersionService 单元测试（使用 fake repositories）。
# ====================================================

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from qa_eval.application.services.document_version_service import (
    DocumentVersionService,
)
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.entities.document_version import DocumentVersion
from qa_eval.domain.exceptions import DocumentVersionInUseError
from qa_eval.domain.repositories.dataset_repository import DatasetRepository
from qa_eval.domain.repositories.document_version_repository import (
    DocumentVersionRepository,
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


class FakeDocumentVersionRepository(DocumentVersionRepository):
    def __init__(self) -> None:
        self._items: dict[UUID, DocumentVersion] = {}
        self._dependencies: dict[UUID, dict[str, int]] = {}

    def add(self, version: DocumentVersion) -> DocumentVersion:
        self._items[version.id] = version
        return version

    def get_by_id(self, version_id: UUID) -> DocumentVersion | None:
        return self._items.get(version_id)

    def list_by_dataset(self, dataset_id: UUID) -> list[DocumentVersion]:
        return [v for v in self._items.values() if v.dataset_id == dataset_id]

    def next_version_label(self, dataset_id: UUID) -> str:
        numbers = [
            int(match.group(1))
            for match in (
                re.fullmatch(r"v(\d+)", v.version)
                for v in self._items.values()
                if v.dataset_id == dataset_id
            )
            if match
        ]
        return f"v{max(numbers) + 1 if numbers else 1}"

    def count_blocking_dependencies(self, version_id: UUID) -> dict[str, int]:
        return self._dependencies.get(version_id, {})

    def set_parsed_artifact_uri(self, version_id: UUID, uri: str) -> bool:
        if version_id not in self._items:
            return False
        self._items[version_id].parsed_artifact_uri = uri
        return True

    def delete(self, version_id: UUID) -> bool:
        if version_id not in self._items:
            return False
        del self._items[version_id]
        return True


CONTENT = "# 数据集说明\n\n本数据集包含 1,000 条记录。\n".encode()


@pytest.fixture
def service(tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path)
    return DocumentVersionService(
        dataset_repository=FakeDatasetRepository(),
        document_version_repository=FakeDocumentVersionRepository(),
        artifact_store=store,
    )


def _make_dataset(service: DocumentVersionService) -> Dataset:
    dataset = Dataset(name="policy.jhsjk_article", modality=["text"])
    return service._dataset_repository.add(dataset)


def test_upload_assigns_sequential_versions(service: DocumentVersionService):
    dataset = _make_dataset(service)

    first = service.upload_document(dataset.id, "readme.md", CONTENT)
    second = service.upload_document(
        dataset.id, "readme-v2.md", CONTENT + "补充\n".encode()
    )

    assert first.version == "v1"
    assert second.version == "v2"
    assert first.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert second.sha256 != first.sha256
    assert (
        service.list_documents(dataset.id) == [second, first]
        or len(service.list_documents(dataset.id)) == 2
    )


def test_upload_strips_directory_components_from_filename(
    service: DocumentVersionService,
):
    dataset = _make_dataset(service)

    version = service.upload_document(dataset.id, "docs/readme.md", CONTENT)

    assert version.filename == "readme.md"


def test_upload_requires_existing_dataset(service: DocumentVersionService):
    with pytest.raises(LookupError, match="Dataset not found"):
        service.upload_document(uuid4(), "readme.md", CONTENT)


@pytest.mark.parametrize(
    ("filename", "content", "match"),
    [
        ("readme.txt", CONTENT, "仅支持 Markdown"),
        ("readme.pdf", CONTENT, "仅支持 Markdown"),
        ("readme.md", b"a" * (2 * 1024 * 1024 + 1), "上限"),
        ("readme.md", b"\xff\xfe\x00invalid", "UTF-8"),
        ("readme.md", b"", "空"),
        ("readme.md", b"  \n\n \t", "空"),
    ],
)
def test_upload_rejects_invalid_documents(
    service: DocumentVersionService, filename, content, match
):
    dataset = _make_dataset(service)

    with pytest.raises(ValueError, match=match):
        service.upload_document(dataset.id, filename, content)

    assert service.list_documents(dataset.id) == []


def test_metadata_reports_line_count_and_parse_status(
    service: DocumentVersionService,
):
    dataset = _make_dataset(service)
    version = service.upload_document(dataset.id, "readme.md", CONTENT)

    metadata = service.get_document_metadata(version.id)

    assert metadata["line_count"] == len(CONTENT.decode("utf-8").splitlines())
    assert metadata["parse_status"] == "NOT_PARSED"
    assert metadata["sha256"] == version.sha256


def test_download_returns_identical_bytes(service: DocumentVersionService):
    dataset = _make_dataset(service)
    version = service.upload_document(dataset.id, "readme.md", CONTENT)

    downloaded, content = service.download_document(version.id)

    assert downloaded.id == version.id
    assert hashlib.sha256(content).hexdigest() == version.sha256


def test_download_missing_document_raises_value_error(
    service: DocumentVersionService,
):
    with pytest.raises(ValueError, match="DocumentVersion not found"):
        service.download_document(uuid4())


def test_delete_document_without_dependencies(
    service: DocumentVersionService,
):
    dataset = _make_dataset(service)
    version = service.upload_document(dataset.id, "readme.md", CONTENT)

    assert service.delete_document(version.id) is True
    assert service.get_document(version.id) is None


def test_delete_missing_document_returns_false(
    service: DocumentVersionService,
):
    assert service.delete_document(uuid4()) is False


def test_delete_blocked_by_report_task_dependency(
    service: DocumentVersionService,
):
    dataset = _make_dataset(service)
    version = service.upload_document(dataset.id, "readme.md", CONTENT)
    service._document_repository._dependencies[version.id] = {
        "document_report_tasks": 2,
    }

    with pytest.raises(DocumentVersionInUseError) as exc_info:
        service.delete_document(version.id)

    assert exc_info.value.code == "DOCUMENT_VERSION_IN_USE"
    assert exc_info.value.dependencies == {"document_report_tasks": 2}
    assert service.get_document(version.id) is not None

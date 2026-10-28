# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:18:45
# @LastEditTime: 2026-10-28
# @Description: Repository 接口可以独立测试业务逻辑，Fake Repository 完全不依赖 PostgreSQL
# ====================================================

from uuid import UUID, uuid4

import pytest

from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.entities.dataset_version import DatasetVersion
from qa_eval.domain.entities.document_version import DocumentVersion
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.domain.repositories.dataset_repository import (
    DatasetRepository,
)
from qa_eval.domain.repositories.dataset_version_repository import (
    DatasetVersionRepository,
)
from qa_eval.domain.repositories.document_version_repository import (
    DocumentVersionRepository,
)


class FakeDatasetRepository(DatasetRepository):

    def __init__(self):
        self.items: dict = {}
        self.deleted: list[UUID] = []

    def add(self, dataset: Dataset) -> Dataset:
        self.items[dataset.id] = dataset
        return dataset

    def get_by_id(self, dataset_id):
        return self.items.get(dataset_id)

    def list_all(self):
        return list(self.items.values())

    def delete(self, dataset_id: UUID) -> bool:
        if dataset_id not in self.items:
            return False
        del self.items[dataset_id]
        self.deleted.append(dataset_id)
        return True


class FakeDatasetVersionRepository(DatasetVersionRepository):

    def __init__(self):
        self.items: dict = {}
        self.deleted: list[UUID] = []
        self.dependencies: dict[UUID, dict[str, int]] = {}

    def add(self, version: DatasetVersion) -> DatasetVersion:
        self.items[version.id] = version
        return version

    def get_by_id(self, version_id: UUID) -> DatasetVersion | None:
        return self.items.get(version_id)

    def list_by_dataset(self, dataset_id: UUID) -> list[DatasetVersion]:
        return [
            v for v in self.items.values() if v.dataset_id == dataset_id
        ]

    def update(self, version: DatasetVersion) -> DatasetVersion:
        self.items[version.id] = version
        return version

    def update_artifact_refs(
        self,
        version_id: UUID,
        artifact_refs: dict,
        content_fingerprint: str | None = None,
    ) -> DatasetVersion:
        return self.items[version_id]

    def get_artifact_refs(self, version_id: UUID) -> dict | None:
        return None

    def count_blocking_dependencies(
        self, version_id: UUID
    ) -> dict[str, int]:
        return self.dependencies.get(version_id, {})

    def delete(self, version_id: UUID) -> bool:
        if version_id not in self.items:
            return False
        del self.items[version_id]
        self.deleted.append(version_id)
        return True


class FakeDocumentVersionRepository(DocumentVersionRepository):

    def __init__(self):
        self.items: dict = {}

    def add(self, version: DocumentVersion) -> DocumentVersion:
        self.items[version.id] = version
        return version

    def get_by_id(self, version_id: UUID) -> DocumentVersion | None:
        return self.items.get(version_id)

    def list_by_dataset(self, dataset_id: UUID) -> list[DocumentVersion]:
        return [
            v for v in self.items.values() if v.dataset_id == dataset_id
        ]

    def next_version_label(self, dataset_id: UUID) -> str:
        return "v1"

    def count_blocking_dependencies(
        self, version_id: UUID
    ) -> dict[str, int]:
        return {}

    def set_parsed_artifact_uri(self, version_id: UUID, uri: str) -> bool:
        return version_id in self.items

    def delete(self, version_id: UUID) -> bool:
        if version_id not in self.items:
            return False
        del self.items[version_id]
        return True


def _service(
    repo: FakeDatasetRepository | None = None,
    version_repo: FakeDatasetVersionRepository | None = None,
) -> tuple[DatasetService, FakeDatasetRepository, FakeDatasetVersionRepository]:
    repo = repo or FakeDatasetRepository()
    version_repo = version_repo or FakeDatasetVersionRepository()
    service = DatasetService(repo, version_repo, FakeDocumentVersionRepository())
    return service, repo, version_repo


def test_register_dataset():
    service, repo, _ = _service()

    dataset = service.register_dataset(
        name="jhsjk_article",
        modality=["text"],
        description="MySQL article dataset",
    )

    assert dataset.name == "jhsjk_article"
    assert dataset.modality == ["text"]
    assert repo.get_by_id(dataset.id) == dataset


def test_get_dataset():
    service, repo, _ = _service()

    dataset = Dataset(
        id=uuid4(),
        name="jhsjk_article",
        modality=["text"],
    )

    repo.add(dataset)

    result = service.get_dataset(dataset.id)

    assert result == dataset


def test_list_datasets():
    service, _, _ = _service()

    service.register_dataset(
        name="jhsjk_article",
        modality=["text"],
    )

    result = service.list_datasets()

    assert len(result) == 1


def test_reject_empty_dataset_name():
    service, _, _ = _service()

    with pytest.raises(ValueError):
        service.register_dataset(
            name="   ",
            modality=["text"],
        )


def test_delete_dataset_without_versions():
    service, repo, _ = _service()

    dataset = service.register_dataset(name="jhsjk_article", modality=["text"])

    assert service.delete_dataset(dataset.id) is True
    assert repo.get_by_id(dataset.id) is None


def test_delete_dataset_removes_clean_versions():
    service, repo, version_repo = _service()

    dataset = service.register_dataset(name="jhsjk_article", modality=["text"])
    version_repo.add(
        DatasetVersion(id=uuid4(), dataset_id=dataset.id, version="v1")
    )
    version_repo.add(
        DatasetVersion(id=uuid4(), dataset_id=dataset.id, version="v2")
    )

    assert service.delete_dataset(dataset.id) is True
    assert repo.get_by_id(dataset.id) is None
    assert version_repo.items == {}


def test_delete_dataset_cascades_document_versions():
    service, repo, _ = _service()
    document_repo = service.document_version_repository

    dataset = service.register_dataset(name="jhsjk_article", modality=["text"])
    document_repo.add(
        DocumentVersion(
            dataset_id=dataset.id,
            version="v1",
            filename="readme.md",
            format="markdown",
            size_bytes=16,
            sha256="0" * 64,
            storage_uri="datasets/x/documents/y/readme.md",
        )
    )

    assert service.delete_dataset(dataset.id) is True
    assert repo.get_by_id(dataset.id) is None
    assert document_repo.list_by_dataset(dataset.id) == []


def test_delete_missing_dataset_returns_false():
    service, _, _ = _service()

    assert service.delete_dataset(uuid4()) is False


def test_delete_dataset_blocked_by_evaluation_dependency():
    service, repo, version_repo = _service()

    dataset = service.register_dataset(name="jhsjk_article", modality=["text"])
    protected = DatasetVersion(
        id=uuid4(), dataset_id=dataset.id, version="v1"
    )
    version_repo.add(protected)
    version_repo.dependencies[protected.id] = {"evaluation_runs": 3}

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        service.delete_dataset(dataset.id)

    assert exc_info.value.code == "DATASET_IN_USE"
    assert exc_info.value.dependencies["evaluation_runs"] == 3
    assert exc_info.value.dependencies["protected_versions"] == 1
    # 被阻止时不得留下任何部分删除
    assert repo.get_by_id(dataset.id) is not None
    assert version_repo.get_by_id(protected.id) is not None
    assert version_repo.deleted == []

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:18:45
# @LastEditTime: 2026-10-19 16:21:46
# @Description: Repository 接口可以独立测试业务逻辑，Fake Repository 完全不依赖 PostgreSQL
# ====================================================

from uuid import uuid4

import pytest

from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.repositories.dataset_repository import (
    DatasetRepository,
)


class FakeDatasetRepository(DatasetRepository):

    def __init__(self):
        self.items: dict = {}

    def add(self, dataset: Dataset) -> Dataset:
        self.items[dataset.id] = dataset
        return dataset

    def get_by_id(self, dataset_id):
        return self.items.get(dataset_id)

    def list_all(self):
        return list(self.items.values())


def test_register_dataset():
    repo = FakeDatasetRepository()
    service = DatasetService(repo)

    dataset = service.register_dataset(
        name="jhsjk_article",
        modality=["text"],
        description="MySQL article dataset",
    )

    assert dataset.name == "jhsjk_article"
    assert dataset.modality == ["text"]
    assert repo.get_by_id(dataset.id) == dataset


def test_get_dataset():
    repo = FakeDatasetRepository()
    service = DatasetService(repo)

    dataset = Dataset(
        id=uuid4(),
        name="jhsjk_article",
        modality=["text"],
    )

    repo.add(dataset)

    result = service.get_dataset(dataset.id)

    assert result == dataset


def test_list_datasets():
    repo = FakeDatasetRepository()
    service = DatasetService(repo)

    service.register_dataset(
        name="jhsjk_article",
        modality=["text"],
    )

    result = service.list_datasets()

    assert len(result) == 1


def test_reject_empty_dataset_name():
    repo = FakeDatasetRepository()
    service = DatasetService(repo)

    with pytest.raises(ValueError):
        service.register_dataset(
            name="   ",
            modality=["text"],
        )

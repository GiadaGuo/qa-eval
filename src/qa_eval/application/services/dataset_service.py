# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:17:12
# @LastEditTime: 2026-10-19 16:17:39
# @Description:
# ====================================================

from uuid import UUID

from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.repositories.dataset_repository import (
    DatasetRepository,
)


class DatasetService:
    def __init__(self, repository: DatasetRepository):
        self.repository = repository

    def register_dataset(
        self,
        name: str,
        modality: list[str],
        description: str | None = None,
    ) -> Dataset:
        dataset = Dataset(
            name=name,
            modality=modality,
            description=description,
        )

        return self.repository.add(dataset)

    def get_dataset(self, dataset_id: UUID) -> Dataset | None:
        return self.repository.get_by_id(dataset_id)

    def list_datasets(self) -> list[Dataset]:
        return self.repository.list_all()

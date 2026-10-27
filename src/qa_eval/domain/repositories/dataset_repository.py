# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:12:13
# @LastEditTime: 2026-10-19 16:12:30
# @Description: 仓储模式（Application Service 只知道 Repository 提供三个能力，不关心数据来自 PostgreSQL、内存还是其他存储）
# ====================================================

from abc import ABC, abstractmethod
from uuid import UUID

from qa_eval.domain.entities.dataset import Dataset


class DatasetRepository(ABC):

    @abstractmethod
    def add(self, dataset: Dataset) -> Dataset:
        """Persist a dataset."""

    @abstractmethod
    def get_by_id(self, dataset_id: UUID) -> Dataset | None:
        """Find a dataset by its ID."""

    @abstractmethod
    def list_all(self) -> list[Dataset]:
        """List registered datasets."""

    @abstractmethod
    def delete(self, dataset_id: UUID) -> bool:
        """Delete a dataset. Returns False if it does not exist."""

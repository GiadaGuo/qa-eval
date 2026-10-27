# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetProfile 仓储接口。
# ====================================================

from abc import ABC, abstractmethod
from uuid import UUID

from qa_eval.contracts import DatasetProfileV1


class DatasetProfileRepository(ABC):

    @abstractmethod
    def add(
        self,
        profile: DatasetProfileV1,
        checksum_sha256: str,
    ) -> DatasetProfileV1:
        """Persist a dataset profile and return the canonical contract."""

    @abstractmethod
    def get_by_dataset_version(
        self,
        dataset_version_id: UUID,
    ) -> DatasetProfileV1 | None:
        """Find a profile by dataset version ID."""

    @abstractmethod
    def list_by_dataset_version(
        self, dataset_version_id: UUID
    ) -> list[DatasetProfileV1]:
        """List profile history, newest first."""

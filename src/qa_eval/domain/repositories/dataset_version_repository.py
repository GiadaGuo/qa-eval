# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetVersion 仓储接口，支持闭环 2 的创建、发布与查询。
# ====================================================

from abc import ABC, abstractmethod
from uuid import UUID

from qa_eval.domain.entities.dataset_version import DatasetVersion


class DatasetVersionRepository(ABC):

    @abstractmethod
    def add(self, version: DatasetVersion) -> DatasetVersion:
        """Persist a new dataset version."""

    @abstractmethod
    def get_by_id(self, version_id: UUID) -> DatasetVersion | None:
        """Find a dataset version by its ID."""

    @abstractmethod
    def list_by_dataset(self, dataset_id: UUID) -> list[DatasetVersion]:
        """List versions for a dataset."""

    @abstractmethod
    def update(self, version: DatasetVersion) -> DatasetVersion:
        """Update an existing dataset version."""

    @abstractmethod
    def update_artifact_refs(
        self,
        version_id: UUID,
        artifact_refs: dict,
        content_fingerprint: str | None = None,
    ) -> DatasetVersion:
        """Update artifact references and optional content fingerprint."""

    @abstractmethod
    def get_artifact_refs(self, version_id: UUID) -> dict | None:
        """Get artifact references for a dataset version."""

    @abstractmethod
    def count_blocking_dependencies(
        self, version_id: UUID
    ) -> dict[str, int]:
        """Count downstream evaluation rows (physical plans, evaluation
        runs, metric results, quality reports, quality contract versions)
        that reference the dataset version and block deletion."""

    @abstractmethod
    def delete(self, version_id: UUID) -> bool:
        """Delete a version together with its manifests and profiles.
        Returns False if it does not exist."""

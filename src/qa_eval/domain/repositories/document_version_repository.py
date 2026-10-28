# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# DocumentVersion 仓储接口：上传分配、查询与引用检查。
# ====================================================

from abc import ABC, abstractmethod
from uuid import UUID

from qa_eval.domain.entities.document_version import DocumentVersion


class DocumentVersionRepository(ABC):
    @abstractmethod
    def add(self, version: DocumentVersion) -> DocumentVersion:
        """Persist a new document version."""

    @abstractmethod
    def get_by_id(self, version_id: UUID) -> DocumentVersion | None:
        """Find a document version by its ID."""

    @abstractmethod
    def list_by_dataset(self, dataset_id: UUID) -> list[DocumentVersion]:
        """List document versions for a dataset."""

    @abstractmethod
    def next_version_label(self, dataset_id: UUID) -> str:
        """Allocate the next automatic version label (v1、v2…)."""

    @abstractmethod
    def count_blocking_dependencies(self, version_id: UUID) -> dict[str, int]:
        """Count downstream rows (document report tasks) that reference
        the document version and block deletion."""

    @abstractmethod
    def set_parsed_artifact_uri(self, version_id: UUID, uri: str) -> bool:
        """Persist the parsed artifact URI into the document JSON.
        Returns False if the version does not exist."""

    @abstractmethod
    def delete(self, version_id: UUID) -> bool:
        """Delete a document version. Returns False if it does not exist."""

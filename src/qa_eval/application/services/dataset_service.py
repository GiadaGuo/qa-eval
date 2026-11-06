# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:17:12
# @LastEditTime: 2026-10-28
# @Description:
# ====================================================

import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from qa_eval.application.services.dataset_deletion_service import DatasetDeletionService
from qa_eval.domain.entities.dataset import Dataset
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

logger = logging.getLogger(__name__)


class DatasetService:
    def __init__(
        self,
        repository: DatasetRepository,
        version_repository: DatasetVersionRepository,
        document_version_repository: DocumentVersionRepository,
        deletion_service: DatasetDeletionService | None = None,
    ):
        self.repository = repository
        self.version_repository = version_repository
        self.document_version_repository = document_version_repository
        self.deletion_service = deletion_service

    def get_deletion_preview(self, dataset_id: UUID) -> dict | None:
        if self.deletion_service is None:
            raise RuntimeError("Dataset deletion preview is not configured")
        return self.deletion_service.preview(dataset_id)

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

    def delete_dataset(self, dataset_id: UUID, *, cascade: bool = False) -> bool:
        """删除数据集及其全部无下游依赖的版本。

        任一版本被评测依赖引用时抛出 DatasetVersionInUseError，
        不做任何删除，保证事务内不留部分删除的数据。
        """

        if cascade:
            if self.deletion_service is None:
                raise RuntimeError("Dataset cascade deletion is not configured")
            return self.deletion_service.cascade_delete(dataset_id)

        dataset = self.repository.get_by_id(dataset_id)

        if dataset is None:
            return False

        versions = self.version_repository.list_by_dataset(dataset_id)
        documents = self.document_version_repository.list_by_dataset(
            dataset_id
        )

        dependencies: dict[str, int] = {}
        protected_versions = 0
        for version in versions:
            counts = self.version_repository.count_blocking_dependencies(
                version.id
            )
            if any(counts.values()):
                protected_versions += 1
                for key, count in counts.items():
                    dependencies[key] = dependencies.get(key, 0) + count

        if protected_versions > 0:
            dependencies["protected_versions"] = protected_versions
            raise DatasetVersionInUseError(
                dependencies, code="DATASET_IN_USE"
            )

        try:
            for version in versions:
                self.version_repository.delete(version.id)
            # 说明文档版本随数据集级联删除（报告任务由 RESTRICT 外键兜底）
            for document in documents:
                self.document_version_repository.delete(document.id)
            self.repository.delete(dataset_id)
        except IntegrityError as exc:
            # 依赖检查与删除之间存在并发新增引用的窗口，由 RESTRICT 兜底
            logger.warning(
                "Concurrent reference detected while deleting dataset %s: %s",
                dataset_id,
                exc,
            )
            raise DatasetVersionInUseError(
                {}, code="DATASET_IN_USE", message="数据集删除时检测到并发引用，请重试"
            ) from exc

        return True

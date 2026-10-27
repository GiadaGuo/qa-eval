# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetVersion SQLAlchemy 仓储实现。
# ====================================================

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from qa_eval.domain.entities.dataset_version import (
    DatasetVersion,
    DatasetVersionStatus,
)
from qa_eval.domain.repositories.dataset_version_repository import (
    DatasetVersionRepository,
)
from qa_eval.infrastructure.db.models.dataset_manifest import (
    DatasetManifestModel,
)
from qa_eval.infrastructure.db.models.dataset_profile import (
    DatasetProfileModel,
)
from qa_eval.infrastructure.db.models.dataset_version import (
    DatasetVersionModel,
)
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    MetricResultModel,
    PhysicalEvaluationPlanModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    QualityReportModel,
)


class SQLAlchemyDatasetVersionRepository(DatasetVersionRepository):

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _to_domain(model: DatasetVersionModel) -> DatasetVersion:
        return DatasetVersion(
            id=model.id,
            dataset_id=model.dataset_id,
            version=model.version,
            status=DatasetVersionStatus(model.status),
            created_at=model.created_at,
            published_at=model.published_at,
        )

    @staticmethod
    def _artifact_refs_from_model(model: DatasetVersionModel) -> dict | None:
        return model.artifact_refs

    def update_artifact_refs(
        self,
        version_id: UUID,
        artifact_refs: dict,
        content_fingerprint: str | None = None,
    ) -> DatasetVersion:
        model = self.session.get(DatasetVersionModel, version_id)
        if model is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")

        model.artifact_refs = artifact_refs
        if content_fingerprint is not None:
            model.content_fingerprint = content_fingerprint
        self.session.flush()
        self.session.refresh(model)
        return self._to_domain(model)

    def get_artifact_refs(self, version_id: UUID) -> dict | None:
        model = self.session.get(DatasetVersionModel, version_id)
        if model is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        return model.artifact_refs

    def add(self, version: DatasetVersion) -> DatasetVersion:
        model = DatasetVersionModel(
            id=version.id,
            dataset_id=version.dataset_id,
            version=version.version,
            status=version.status.value,
            published_at=version.published_at,
        )
        self.session.add(model)
        self.session.flush()
        self.session.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, version_id: UUID) -> DatasetVersion | None:
        model = self.session.get(DatasetVersionModel, version_id)
        if model is None:
            return None
        return self._to_domain(model)

    def list_by_dataset(self, dataset_id: UUID) -> list[DatasetVersion]:
        stmt = (
            select(DatasetVersionModel)
            .where(DatasetVersionModel.dataset_id == dataset_id)
            .order_by(DatasetVersionModel.created_at.desc())
        )
        models = self.session.scalars(stmt).all()
        return [self._to_domain(model) for model in models]

    def update(self, version: DatasetVersion) -> DatasetVersion:
        model = self.session.get(DatasetVersionModel, version.id)
        if model is None:
            raise ValueError(f"DatasetVersion not found: {version.id}")

        model.status = version.status.value
        model.published_at = version.published_at
        self.session.flush()
        self.session.refresh(model)
        return self._to_domain(model)

    def count_blocking_dependencies(
        self, version_id: UUID
    ) -> dict[str, int]:
        """统计会阻止版本物理删除的下游评测依赖行数。"""

        def _count(model, column) -> int:
            stmt = (
                select(func.count())
                .select_from(model)
                .where(column == version_id)
            )
            return self.session.scalar(stmt) or 0

        return {
            "physical_evaluation_plans": _count(
                PhysicalEvaluationPlanModel,
                PhysicalEvaluationPlanModel.dataset_version_id,
            ),
            "evaluation_runs": _count(
                EvaluationRunModel,
                EvaluationRunModel.dataset_version_id,
            ),
            "metric_results": _count(
                MetricResultModel,
                MetricResultModel.dataset_version_id,
            ),
            "quality_reports": _count(
                QualityReportModel,
                QualityReportModel.dataset_version_id,
            ),
            "quality_contract_versions": _count(
                QualityContractVersionModel,
                QualityContractVersionModel.target_dataset_version_id,
            ),
        }

    def delete(self, version_id: UUID) -> bool:
        """删除版本及其内部附属数据（manifest / profile），同一事务内执行。"""

        model = self.session.get(DatasetVersionModel, version_id)

        if model is None:
            return False

        # 外键为 RESTRICT，附属行必须先于版本行删除
        self.session.execute(
            delete(DatasetManifestModel).where(
                DatasetManifestModel.dataset_version_id == version_id
            )
        )
        self.session.execute(
            delete(DatasetProfileModel).where(
                DatasetProfileModel.dataset_version_id == version_id
            )
        )
        self.session.delete(model)
        self.session.flush()

        return True

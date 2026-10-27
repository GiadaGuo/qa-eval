# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetProfile SQLAlchemy 仓储实现。
# ====================================================

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.contracts import DatasetProfileV1
from qa_eval.domain.repositories.dataset_profile_repository import (
    DatasetProfileRepository,
)
from qa_eval.infrastructure.db.models.dataset_profile import (
    DatasetProfileModel,
)


class SQLAlchemyDatasetProfileRepository(DatasetProfileRepository):
    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        profile: DatasetProfileV1,
        checksum_sha256: str,
    ) -> DatasetProfileV1:
        model = DatasetProfileModel(
            id=profile.profile_id,
            dataset_version_id=profile.dataset_version_id,
            document=profile.model_dump(mode="json"),
            checksum_sha256=checksum_sha256,
            profiler_key=profile.profiler.key,
            profiler_version=profile.profiler.version,
            config_fingerprint=profile.profiler.config_fingerprint,
        )
        self.session.add(model)
        self.session.flush()
        self.session.refresh(model)
        return DatasetProfileV1.model_validate(model.document)

    def get_by_dataset_version(
        self,
        dataset_version_id: UUID,
    ) -> DatasetProfileV1 | None:
        stmt = select(DatasetProfileModel).where(
            DatasetProfileModel.dataset_version_id == dataset_version_id
        )
        model = self.session.scalars(
            stmt.order_by(DatasetProfileModel.created_at.desc())
        ).first()
        if model is None:
            return None
        return DatasetProfileV1.model_validate(model.document)

    def list_by_dataset_version(
        self, dataset_version_id: UUID
    ) -> list[DatasetProfileV1]:
        stmt = (
            select(DatasetProfileModel)
            .where(DatasetProfileModel.dataset_version_id == dataset_version_id)
            .order_by(DatasetProfileModel.created_at.desc())
        )
        return [
            DatasetProfileV1.model_validate(model.document)
            for model in self.session.scalars(stmt).all()
        ]

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:14:29
# @LastEditTime: 2026-10-19 16:16:02
# @Description: 
# ====================================================

from uuid import UUID

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.orm import Session

from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.repositories.dataset_repository import (
    DatasetRepository,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel


class SQLAlchemyDatasetRepository(DatasetRepository):

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _to_domain(model: DatasetModel) -> Dataset:
        return Dataset(
            id=model.id,
            name=model.name,
            description=model.description,
            modality=list(model.modality),
            data_type=model.data_type,
            owner=model.owner,
            enabled=model.enabled,
            source_kind=model.source_kind,
            storage_path=model.storage_path,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def add(self, dataset: Dataset) -> Dataset:
        model = DatasetModel(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            modality=list(dataset.modality),
            data_type=dataset.data_type,
            owner=dataset.owner,
        )

        # 把 ORM 对象放进 Session
        self.session.add(model) 
        # 执行 INSERT，但不提交事务
        self.session.flush()
        # 重新读取数据库字段，例如 created_at 的默认值
        self.session.refresh(model)

        return self._to_domain(model)

    def get_by_id(self, dataset_id: UUID) -> Dataset | None:
        model = self.session.get(DatasetModel, dataset_id)

        if model is None:
            return None

        return self._to_domain(model)

    def list_all(self) -> list[Dataset]:
        stmt = select(DatasetModel).order_by(
            DatasetModel.created_at,
            DatasetModel.id,
        )

        models = self.session.scalars(stmt).all()

        return [self._to_domain(model) for model in models]

    def allocate_version_number(self, dataset_id: UUID) -> int:
        return self.session.execute(
            update(DatasetModel)
            .where(DatasetModel.id == dataset_id)
            .values(version_sequence=DatasetModel.version_sequence + 1)
            .returning(DatasetModel.version_sequence)
        ).scalar_one()

    def reserve_version_number(self, dataset_id: UUID, number: int) -> None:
        if number > 999_999_999:
            raise ValueError("Version number is too large")
        allocated = self.session.execute(
            update(DatasetModel)
            .where(DatasetModel.id == dataset_id, DatasetModel.version_sequence < number)
            .values(version_sequence=number)
            .returning(DatasetModel.version_sequence)
        ).scalar_one_or_none()
        if allocated is None:
            raise ValueError("Version number has already been allocated")

    def update_metadata(self, dataset_id: UUID, changes: dict) -> Dataset | None:
        model = self.session.get(DatasetModel, dataset_id)
        if model is None:
            return None
        for name, value in changes.items():
            setattr(model, name, value)
        self.session.flush()
        return self._to_domain(model)

    def search(self, *, q, data_type, modality, source_kind, enabled, page, size):
        stmt = select(DatasetModel)
        if q:
            stmt = stmt.where(
                DatasetModel.name.icontains(q, autoescape=True)
                | DatasetModel.owner.icontains(q, autoescape=True)
            )
        if data_type:
            stmt = stmt.where(DatasetModel.data_type == data_type)
        if modality:
            stmt = stmt.where(cast(DatasetModel.modality, String).contains(
                f'"{modality}"', autoescape=True
            ))
        if source_kind == "unconfigured":
            stmt = stmt.where(DatasetModel.source_kind.is_(None))
        elif source_kind:
            stmt = stmt.where(DatasetModel.source_kind == source_kind)
        if enabled is not None:
            stmt = stmt.where(DatasetModel.enabled == enabled)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = self.session.scalars(
            stmt.order_by(DatasetModel.created_at.desc(), DatasetModel.id)
            .offset((page - 1) * size).limit(size)
        )
        return [self._to_domain(row) for row in rows], total

    def delete(self, dataset_id: UUID) -> bool:
        model = self.session.get(DatasetModel, dataset_id)

        if model is None:
            return False

        # 版本必须已由上层在同一事务中先行删除，否则 RESTRICT 会拦截
        self.session.delete(model)
        self.session.flush()

        return True

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:14:29
# @LastEditTime: 2026-10-19 16:16:02
# @Description: 
# ====================================================

from uuid import UUID

from sqlalchemy import select
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
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def add(self, dataset: Dataset) -> Dataset:
        model = DatasetModel(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            modality=list(dataset.modality),
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

    def delete(self, dataset_id: UUID) -> bool:
        model = self.session.get(DatasetModel, dataset_id)

        if model is None:
            return False

        # 版本必须已由上层在同一事务中先行删除，否则 RESTRICT 会拦截
        self.session.delete(model)
        self.session.flush()

        return True

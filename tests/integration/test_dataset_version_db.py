# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 17:14:20
# @LastEditTime: 2026-10-19 17:14:31
# @Description: 验证联合唯一约束确实生效
# - 故意触发 IntegrityError。测试 Fixture 在结束时会回滚外层事务；
# ====================================================

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import (
    DatasetVersionModel,
)


def test_dataset_version_unique_constraint(db_session):
    dataset = DatasetModel(
        id=uuid4(),
        name="jhsjk_article",
        modality=["text"],
    )

    db_session.add(dataset)
    db_session.flush()

    first = DatasetVersionModel(
        id=uuid4(),
        dataset_id=dataset.id,
        version="v1",
        status="DRAFT",
    )

    db_session.add(first)
    db_session.flush()

    duplicate = DatasetVersionModel(
        id=uuid4(),
        dataset_id=dataset.id,
        version="v1",
        status="DRAFT",
    )

    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.flush()

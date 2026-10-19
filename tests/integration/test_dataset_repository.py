# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:22:44
# @LastEditTime: 2026-10-19 16:26:05
# @Description: 仓储层（Repository）的集成测试，测 `SQLAlchemyDatasetRepository`，也就是「领域实体 Dataset ↔ SQLAlchemy 数据库」这一层能不能正常工作
# ====================================================

from uuid import uuid4

from qa_eval.domain.entities.dataset import Dataset
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)


def test_repository_add_and_get(db_session):
    """
    测试：新增一条 Dataset，再通过 ID 查询这条数据
    流程：

    1. 构造领域实体 `Dataset`（domain 层对象，不是 SQLAlchemy Model）
    2. 调用 `repo.add()` 存入数据库
    3. 用 `get_by_id()` 根据 id 查回来
    4. 断言：查到了、id 对上、name 正确、自动生成的 `created_at` 有值

    重点验证：

    - add 方法可以正常把领域实体落库
    - get_by_id 可以正常从数据库读出，并**转回领域实体 Dataset**
    - 自动时间字段 `created_at` 填充逻辑正常
    - 实体 ↔ ORM Model 的转换（mapping）没有丢字段
    """
    repo = SQLAlchemyDatasetRepository(db_session)

    dataset = Dataset(
        id=uuid4(),
        name="jhsjk_article",
        description="Chinese article dataset",
        modality=["text"],
    )

    saved = repo.add(dataset)
    loaded = repo.get_by_id(saved.id)

    assert loaded is not None
    assert loaded.id == saved.id
    assert loaded.name == "jhsjk_article"
    assert loaded.created_at is not None


def test_repository_get_missing(db_session):
    """
    测试：查询一个不存在的 id，返回 None，而不是抛异常
    边界 case：给一个库里没有的 uuid，`get_by_id` 预期返回 `None`。
    保证上层业务可以用 `if result is None` 判断，不会直接炸。
    """
    repo = SQLAlchemyDatasetRepository(db_session)

    result = repo.get_by_id(uuid4())

    assert result is None


def test_repository_list_all(db_session):
    """
    测试：list_all 能查出所有已保存的数据集
    1. 连续 add 两条不同的 Dataset
    2. 调用 `list_all()` 获取全部
    3. 收集名字集合，断言两条都在结果里

    验证：

    - 批量查询接口正常
    - 多条写入之后，列表查询不会丢数据
    - 简单的结果正确性校验
    """
    repo = SQLAlchemyDatasetRepository(db_session)

    repo.add(
        Dataset(
            name="jhsjk_article",
            modality=["text"],
        )
    )

    repo.add(
        Dataset(
            name="multimodal_demo",
            modality=["text", "image"],
        )
    )

    results = repo.list_all()

    names = {item.name for item in results}

    assert "jhsjk_article" in names
    assert "multimodal_demo" in names

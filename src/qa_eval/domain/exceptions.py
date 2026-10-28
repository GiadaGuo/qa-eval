# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 领域异常：删除数据集/版本时会破坏历史评测可追溯性的场景。
# ====================================================

from __future__ import annotations

_DATASET_MESSAGE = (
    "该数据集包含被评测依赖引用的版本，无法删除"
)
_VERSION_MESSAGE = "该数据集版本已被评测任务引用，无法删除"
_DOCUMENT_VERSION_MESSAGE = "该说明文档版本已被评测任务引用，无法删除"


class DatasetVersionInUseError(Exception):
    """下游评测依赖（Evaluation Run / Metric Result / Quality Report 等）
    仍引用目标数据集版本时抛出，用于阻止物理删除。
    """

    def __init__(
        self,
        dependencies: dict[str, int],
        *,
        code: str = "DATASET_VERSION_IN_USE",
        message: str | None = None,
    ) -> None:
        self.dependencies = {
            key: count for key, count in dependencies.items() if count
        }
        self.code = code
        self.message = message or (
            _DATASET_MESSAGE if code == "DATASET_IN_USE" else _VERSION_MESSAGE
        )
        super().__init__(self.message)


class DocumentVersionInUseError(Exception):
    """说明文档版本仍被 Document Report Task 引用时抛出，
    用于阻止物理删除。
    """

    def __init__(
        self,
        dependencies: dict[str, int],
        *,
        code: str = "DOCUMENT_VERSION_IN_USE",
        message: str | None = None,
    ) -> None:
        self.dependencies = {
            key: count for key, count in dependencies.items() if count
        }
        self.code = code
        self.message = message or _DOCUMENT_VERSION_MESSAGE
        super().__init__(self.message)

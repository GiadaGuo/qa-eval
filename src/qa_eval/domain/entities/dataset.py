# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:10:20
# @LastEditTime: 2026-10-19 16:10:57
# @Description: 业务实体，区别于DatasetModel是数据库 ORM 映射
# ====================================================

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass
class Dataset:
    name: str
    modality: list[str]
    description: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Dataset name cannot be empty")

        if not self.modality:
            raise ValueError("Dataset must have at least one modality")

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 17:00:20
# @LastEditTime: 2026-10-19 17:00:32
# @Description: 
# ====================================================

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4


class DatasetVersionStatus(str, Enum):
    DRAFT = "DRAFT"
    REGISTERED = "REGISTERED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


@dataclass
class DatasetVersion:
    dataset_id: UUID
    version: str
    id: UUID = field(default_factory=uuid4)
    status: DatasetVersionStatus = DatasetVersionStatus.DRAFT
    created_at: datetime | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Dataset version cannot be empty")

    def mark_registered(self) -> None:
        if self.status != DatasetVersionStatus.DRAFT:
            raise ValueError(
                "Only DRAFT versions can be registered"
            )
        self.status = DatasetVersionStatus.REGISTERED

    def publish(self, published_at: datetime) -> None:
        if self.status != DatasetVersionStatus.REGISTERED:
            raise ValueError(
                "Only REGISTERED versions can be published"
            )

        if published_at.tzinfo is None:
            raise ValueError(
                "Publication timestamp must be timezone-aware"
            )

        self.status = DatasetVersionStatus.PUBLISHED
        self.published_at = published_at

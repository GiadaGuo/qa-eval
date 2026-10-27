# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 09:51:05
# @LastEditTime: 2026-10-21 11:36:21
# @Description: 
# ====================================================

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from qa_eval.domain.entities.sample_record import SampleRecord


@dataclass(frozen=True)
class AdapterContext:
    dataset_version_id: UUID
    source_uri: str
    input_fingerprint: str


class DatasetAdapter(Protocol):
    def adapt(
        self,
        raw_record: Mapping[str, Any],
        context: AdapterContext,
    ) -> SampleRecord:
        ...

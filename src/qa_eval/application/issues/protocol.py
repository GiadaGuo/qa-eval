from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityIssueV1, RuleEvaluationV1
from qa_eval.domain.ports.artifact_store import ArtifactStore


@dataclass(frozen=True)
class IssueBuildContext:
    result: MetricResultV1
    evaluations: tuple[RuleEvaluationV1, ...]
    severity_by_item: dict[str, str]
    created_at: datetime
    artifact_store: ArtifactStore


class IssueBuilder(Protocol):
    key: str
    version: str
    replaces_threshold_summary: bool

    def supports(self, result: MetricResultV1) -> bool: ...

    def build(self, context: IssueBuildContext) -> list[QualityIssueV1]: ...

from __future__ import annotations

from qa_eval.application.issues.content_consistency import (
    ContentConsistencyIssueBuilder,
)
from qa_eval.application.issues.dataset_card import DatasetCardIssueBuilder
from qa_eval.application.issues.duplicate_group import DuplicateGroupIssueBuilder
from qa_eval.application.issues.field_relation_consistency import (
    FieldRelationConsistencyIssueBuilder,
)
from qa_eval.application.issues.policy_document_format import (
    PolicyDocumentFormatIssueBuilder,
)
from qa_eval.application.issues.policy_effectiveness import (
    PolicyEffectivenessIssueBuilder,
)
from qa_eval.application.issues.protocol import IssueBuildContext, IssueBuilder
from qa_eval.application.issues.sensitive_entity import SensitiveEntityIssueBuilder
from qa_eval.contracts.quality import QualityIssueV1


class IssueBuilderRegistry:
    """Select application-layer issue strategies without adding domain entities."""

    def __init__(self, builders: tuple[IssueBuilder, ...] | None = None) -> None:
        self._builders = builders or (
            DuplicateGroupIssueBuilder(),
            DatasetCardIssueBuilder(),
            SensitiveEntityIssueBuilder(),
        )

    def build_native(
        self, result, evaluation, created_at, artifact_store
    ) -> list[QualityIssueV1]:
        for builder in (
            PolicyEffectivenessIssueBuilder(),
            PolicyDocumentFormatIssueBuilder(),
            ContentConsistencyIssueBuilder(),
            FieldRelationConsistencyIssueBuilder(),
        ):
            if builder.supports(result):
                return builder.build(result, evaluation, created_at, artifact_store)
        return []

    def build(self, context: IssueBuildContext) -> list[QualityIssueV1]:
        issues: list[QualityIssueV1] = []
        for builder in self._builders:
            if builder.supports(context.result):
                issues.extend(builder.build(context))
        return sorted(issues, key=lambda item: item.dedup_key)

    def replaces_threshold_summary(self, context: IssueBuildContext) -> bool:
        return any(
            builder.supports(context.result) and builder.replaces_threshold_summary
            for builder in self._builders
        )

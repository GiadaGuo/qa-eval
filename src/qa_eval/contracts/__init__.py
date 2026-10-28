"""Versioned wire contracts shared by qa-eval interfaces and artifacts."""

from qa_eval.contracts.artifacts import ArtifactRef, Checksum
from qa_eval.contracts.datasets import (
    DatasetManifestV2,
    DatasetProfileV1,
    NormalizationRunV1,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.document import DocumentQualityReportV1
from qa_eval.contracts.execution import (
    EvaluationRunV1,
    LogicalEvaluationPlanV1,
    PhysicalEvaluationPlanV1,
    StageRunV1,
)
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.proposals import (
    ApplicabilityDecisionV1,
    EvaluationProposalV1,
    EvaluationProposalValidationV1,
    EvaluationRecommendationV1,
)
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    QualityReportV1_1,
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

__all__ = [
    "ApplicabilityDecisionV1",
    "ArtifactRef",
    "CalculatorDescriptorV1",
    "Checksum",
    "DatasetManifestV2",
    "DatasetProfileV1",
    "DocumentQualityReportV1",
    "EvaluationProposalV1",
    "EvaluationProposalValidationV1",
    "EvaluationRecommendationV1",
    "EvaluationRunV1",
    "IssueIndexV1",
    "LogicalEvaluationPlanV1",
    "MetricCalculatorBindingV1",
    "MetricDefinitionV1",
    "MetricResultV1",
    "NormalizationRunV1",
    "PhysicalEvaluationPlanV1",
    "QualityContractVersionV1",
    "QualityIssueV1",
    "QualityReportV1",
    "QualityReportV1_1",
    "RejectedRecordV1",
    "RuleEvaluationV1",
    "SampleRecordV2",
    "StageRunV1",
]

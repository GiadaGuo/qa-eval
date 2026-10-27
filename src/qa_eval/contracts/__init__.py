"""Versioned wire contracts shared by qa-eval interfaces and artifacts."""

from qa_eval.contracts.artifacts import ArtifactRef, Checksum
from qa_eval.contracts.datasets import (
    DatasetManifestV2,
    DatasetProfileV1,
    NormalizationRunV1,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.execution import EvaluationRunV1, PhysicalEvaluationPlanV1, StageRunV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

__all__ = [
    "ArtifactRef",
    "CalculatorDescriptorV1",
    "Checksum",
    "DatasetManifestV2",
    "DatasetProfileV1",
    "IssueIndexV1",
    "MetricCalculatorBindingV1",
    "MetricDefinitionV1",
    "MetricResultV1",
    "PhysicalEvaluationPlanV1",
    "EvaluationRunV1",
    "StageRunV1",
    "NormalizationRunV1",
    "QualityContractVersionV1",
    "QualityIssueV1",
    "QualityReportV1",
    "RejectedRecordV1",
    "RuleEvaluationV1",
    "SampleRecordV2",
]

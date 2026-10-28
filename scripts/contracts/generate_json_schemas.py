"""Generate checked-in JSON Schemas from canonical Pydantic wire contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from qa_eval.contracts.artifacts import ArtifactRef
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
    RuleEvaluationV1,
)
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "qa-eval"

SCHEMAS: dict[Path, type[BaseModel]] = {
    Path("artifact-ref/1.0.schema.json"): ArtifactRef,
    Path("dataset-manifest/2.0.schema.json"): DatasetManifestV2,
    Path("sample-record/2.0.schema.json"): SampleRecordV2,
    Path("rejected-record/1.0.schema.json"): RejectedRecordV1,
    Path("normalization-run/1.0.schema.json"): NormalizationRunV1,
    Path("dataset-profile/1.0.schema.json"): DatasetProfileV1,
    Path("metric-result/1.0.schema.json"): MetricResultV1,
    Path("document-quality-report/1.0.schema.json"): DocumentQualityReportV1,
    Path("applicability-decision/1.0.schema.json"): ApplicabilityDecisionV1,
    Path("evaluation-proposal/1.0.schema.json"): EvaluationProposalV1,
    Path(
        "evaluation-proposal-validation/1.0.schema.json"
    ): EvaluationProposalValidationV1,
    Path("evaluation-recommendation/1.0.schema.json"): EvaluationRecommendationV1,
    Path("physical-evaluation-plan/1.0.schema.json"): PhysicalEvaluationPlanV1,
    Path("evaluation-run/1.0.schema.json"): EvaluationRunV1,
    Path("stage-run/1.0.schema.json"): StageRunV1,
    Path("metric-definition/1.0.schema.json"): MetricDefinitionV1,
    Path("calculator-descriptor/1.0.schema.json"): CalculatorDescriptorV1,
    Path("metric-calculator-binding/1.0.schema.json"): MetricCalculatorBindingV1,
    Path("quality-contract/1.0.schema.json"): QualityContractVersionV1,
    Path("rule-evaluation/1.0.schema.json"): RuleEvaluationV1,
    Path("quality-issue/1.0.schema.json"): QualityIssueV1,
    Path("issue-index/1.0.schema.json"): IssueIndexV1,
    Path("quality-report/1.0.schema.json"): QualityReportV1,
}


def render_schema(relative_path: Path, model: type[BaseModel]) -> str:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"qa-eval://schemas/{relative_path.as_posix()}"
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when a checked-in schema differs from generated output",
    )
    args = parser.parse_args()

    stale: list[str] = []
    for relative_path, model in SCHEMAS.items():
        destination = SCHEMA_ROOT / relative_path
        rendered = render_schema(relative_path, model)
        if args.check:
            if not destination.exists() or destination.read_text() != rendered:
                stale.append(str(destination.relative_to(REPOSITORY_ROOT)))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered)

    if stale:
        print("Stale or missing generated schemas:")
        for path in stale:
            print(f"- {path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

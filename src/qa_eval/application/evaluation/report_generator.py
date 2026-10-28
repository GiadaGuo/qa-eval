from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)

IdFactory = Callable[[], UUID]


@dataclass(frozen=True)
class MetricCatalogEntry:
    key: str
    version: str
    name: str
    dimension: str


def _gate_status(
    contract: QualityContractVersionV1,
    evaluations: list[RuleEvaluationV1],
) -> str:
    item_by_key = {item.key: item for item in contract.items}
    if any(
        evaluation.status == "FAIL"
        and item_by_key[evaluation.contract_item_key].severity
        in contract.gate.fail_on_severities
        for evaluation in evaluations
    ):
        return "FAIL"
    if any(evaluation.status == "UNKNOWN" for evaluation in evaluations):
        return contract.gate.unknown_policy
    return "PASS"


def build_quality_report(
    contract: QualityContractVersionV1,
    results: list[MetricResultV1],
    evaluations: list[RuleEvaluationV1],
    issues: list[QualityIssueV1],
    issue_indices: dict[UUID, IssueIndexV1],
    catalog: list[MetricCatalogEntry],
    *,
    execution_status: str,
    id_factory: IdFactory,
    generated_at: datetime,
) -> QualityReportV1:
    """Build an immutable report only from committed upstream facts."""

    if contract.status != "PUBLISHED":
        raise ValueError("only PUBLISHED contracts can produce reports")
    result_by_metric = {
        (item.metric.key, item.metric.version): item for item in results
    }
    catalog_by_metric = {(item.key, item.version): item for item in catalog}
    planned_items = {
        (item.metric.key, item.metric.version): item for item in contract.items
    }
    planned_metrics = set(planned_items)
    if unknown := planned_metrics - set(catalog_by_metric):
        raise ValueError(f"catalog metadata missing for metrics: {sorted(unknown)}")

    run_ids = {item.run_id for item in results}
    dataset_version_ids = {item.dataset_version_id for item in results}
    if len(run_ids) != 1 or len(dataset_version_ids) != 1:
        raise ValueError("all MetricResults must belong to one run and dataset version")
    [run_id] = run_ids
    [dataset_version_id] = dataset_version_ids

    evaluations_by_result: dict[UUID, list[RuleEvaluationV1]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.run_id != run_id:
            raise ValueError("RuleEvaluation run_id does not match report run")
        if evaluation.contract_version_id != contract.contract_version_id:
            raise ValueError("RuleEvaluation contract_version_id does not match")
        evaluations_by_result[evaluation.metric_result_id].append(evaluation)

    issues_by_result: dict[UUID, list[QualityIssueV1]] = defaultdict(list)
    for issue in issues:
        if issue.run_id != run_id:
            raise ValueError("QualityIssue run_id does not match report run")
        issues_by_result[issue.source.metric_result_id].append(issue)

    metric_sections = []
    for metric_key in sorted(result_by_metric):
        result = result_by_metric[metric_key]
        metadata = catalog_by_metric.get(metric_key)
        if metadata is None:
            raise ValueError(f"catalog metadata missing for metric {metric_key}")
        index = issue_indices.get(result.metric_result_id)
        if index is None:
            raise ValueError(f"IssueIndex missing for result {result.metric_result_id}")
        metric_issues = issues_by_result[result.metric_result_id]
        by_code = Counter(item.code for item in metric_issues)
        by_scope = Counter(item.scope for item in metric_issues)
        confirmed_count = sum(
            item.determination == "CONFIRMED" for item in metric_issues
        )
        candidate_count = sum(
            item.determination == "CANDIDATE" for item in metric_issues
        )
        if (
            confirmed_count != index.summary.confirmed_count
            or candidate_count != index.summary.candidate_count
        ):
            raise ValueError("IssueIndex summary does not match QualityIssues")
        observations = [
            {"key": item.key, "value": item.value, "unit": item.unit}
            for item in result.observations
        ]
        # 以契约 item 声明的 measurement 为主测量，其余观测为该次执行
        # 附带输出的诊断字段，不得当作独立计算方法展示。
        contract_item = planned_items.get(metric_key)
        measurement = None
        if contract_item is not None:
            measurement = next(
                (
                    item
                    for item in observations
                    if item["key"] == contract_item.metric.measurement
                ),
                None,
            )
        diagnostics = [
            item
            for item in observations
            if measurement is None or item is not measurement
        ]
        metric_sections.append(
            {
                "metric": {
                    "key": metadata.key,
                    "version": metadata.version,
                    "name": metadata.name,
                },
                "metric_result_id": result.metric_result_id,
                "status": result.status,
                "measurement": measurement,
                "diagnostics": diagnostics,
                "observations": observations,
                "rule_evaluations": [
                    {
                        "rule_evaluation_id": item.rule_evaluation_id,
                        "contract_item_key": item.contract_item_key,
                        "status": item.status,
                    }
                    for item in evaluations_by_result[result.metric_result_id]
                ],
                "issue_summary": {
                    "confirmed_count": confirmed_count,
                    "candidate_count": candidate_count,
                    "by_code": dict(sorted(by_code.items())),
                    "by_scope": dict(sorted(by_scope.items())),
                },
                "issue_index_id": index.issue_index_id,
                "evidence_ids": [item.evidence_id for item in result.evidence],
            }
        )

    status_counts = Counter(item.status for item in results)
    not_evaluated = len(planned_metrics - set(result_by_metric))
    dimension_totals = Counter(
        catalog_by_metric[item].dimension for item in planned_metrics
    )
    dimension_evaluated = Counter(
        catalog_by_metric[key].dimension
        for key, result in result_by_metric.items()
        if result.status == "OK"
    )
    dimensions = []
    for dimension, total in sorted(dimension_totals.items()):
        evaluated = dimension_evaluated[dimension]
        status = (
            "FULL_COVERAGE"
            if evaluated == total
            else "NOT_EVALUATED"
            if evaluated == 0
            else "PARTIAL_COVERAGE"
        )
        dimensions.append(
            {
                "key": dimension,
                "status": status,
                "evaluated_metric_count": evaluated,
                "total_metric_count": total,
            }
        )

    confirmed_count = sum(item.determination == "CONFIRMED" for item in issues)
    candidate_count = sum(item.determination == "CANDIDATE" for item in issues)
    open_count = sum(item.status == "OPEN" for item in issues)
    return QualityReportV1.model_validate(
        {
            "schema_version": "1.0",
            "report_id": id_factory(),
            "run_id": run_id,
            "dataset_version_id": dataset_version_id,
            "contract_version_id": contract.contract_version_id,
            "generator": {"key": "qa-eval-report", "version": "1.0.0"},
            "execution_status": execution_status,
            "gate_status": _gate_status(contract, evaluations),
            "coverage": {
                "evaluated": status_counts["OK"],
                "not_applicable": status_counts["NOT_APPLICABLE"],
                "missing": status_counts["MISSING"],
                "error": status_counts["ERROR"],
                "not_evaluated": not_evaluated,
            },
            "dimensions": dimensions,
            "metrics": metric_sections,
            "issue_summary": {
                "confirmed_count": confirmed_count,
                "candidate_count": candidate_count,
                "open_count": open_count,
            },
            "rendered_artifact_ids": [],
            "generated_at": generated_at,
        }
    )

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
    QualityReportV1_1,
    RuleEvaluationV1,
)

IdFactory = Callable[[], UUID]


@dataclass(frozen=True)
class MetricCatalogEntry:
    key: str
    version: str
    name: str
    dimension: str
    parent_metric_key: str | None = None
    measurement_key: str | None = None
    measurement_direction: str | None = None
    measurement_minimum: float | None = None
    measurement_maximum: float | None = None
    guide_status: str = "NOT_EVALUATED"
    guide_reason: str = "Metric was not selected for this evaluation run"
    weight: float = 1.0


def _axis_for(metric_key: str) -> str | None:
    axis = metric_key[:4]
    return axis if axis in {f"020{i}" for i in range(1, 9)} else None


def _normalized_score(value, entry: MetricCatalogEntry) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if entry.measurement_direction not in {"HIGHER_IS_BETTER", "LOWER_IS_BETTER"}:
        return None
    if entry.measurement_minimum != 0 or entry.measurement_maximum != 1:
        return None
    ratio = max(0.0, min(1.0, float(value)))
    if entry.measurement_direction == "LOWER_IS_BETTER":
        ratio = 1.0 - ratio
    return round(100.0 * ratio, 4)


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
) -> QualityReportV1_1:
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
    child_parent_keys = {
        item.parent_metric_key for item in catalog if item.parent_metric_key is not None
    }
    leaf_catalog = [
        item
        for item in catalog
        if item.key not in child_parent_keys and _axis_for(item.key) is not None
    ]
    scores: dict[tuple[str, str], float] = {}
    coverage_items = []
    for entry in sorted(leaf_catalog, key=lambda item: (item.key, item.version)):
        key = (entry.key, entry.version)
        result = result_by_metric.get(key)
        score = None
        status = entry.guide_status
        reason = entry.guide_reason
        if result is None and status == "APPLICABLE":
            status = "NOT_EVALUATED"
            reason = "Metric was applicable but produced no MetricResult"
        if result is not None:
            if result.status == "OK":
                observation = next(
                    (item for item in result.observations if item.key == entry.measurement_key),
                    None,
                )
                score = None if observation is None else _normalized_score(observation.value, entry)
                status = "EVALUATED"
                reason = (
                    "Metric completed and is scoreable"
                    if score is not None
                    else "Metric completed but its primary measurement is not scoreable"
                )
            elif result.status == "PARTIAL":
                status = "PARTIAL"
                reason = "Metric completed with partial coverage and was excluded from scoring"
            else:
                status = result.status
                reason = f"MetricResult status is {result.status}"
        if score is not None:
            scores[key] = score
        coverage_items.append(
            {
                "metric_key": entry.key,
                "metric_version": entry.version,
                "metric_name": entry.name,
                "parent_metric_key": entry.parent_metric_key,
                "status": status,
                "score": score,
                "reason": reason,
            }
        )

    applicable = [item for item in leaf_catalog if item.guide_status != "NOT_APPLICABLE"]
    denominator = sum(item.weight for item in applicable)
    included = [item for item in applicable if (item.key, item.version) in scores]
    included_weight = sum(item.weight for item in included)
    effective_coverage = 0.0 if denominator == 0 else included_weight / denominator
    evaluated_score = (
        None
        if included_weight == 0
        else sum(scores[(item.key, item.version)] * item.weight for item in included)
        / included_weight
    )
    radar = []
    for axis in [f"020{i}" for i in range(1, 9)]:
        axis_applicable = [item for item in applicable if _axis_for(item.key) == axis]
        axis_included = [
            item for item in axis_applicable if (item.key, item.version) in scores
        ]
        axis_total = sum(item.weight for item in axis_applicable)
        axis_weight = sum(item.weight for item in axis_included)
        radar.append(
            {
                "dimension": axis,
                "score": None
                if axis_weight == 0
                else sum(scores[(item.key, item.version)] * item.weight for item in axis_included)
                / axis_weight,
                "coverage": 0.0 if axis_total == 0 else axis_weight / axis_total,
            }
        )

    recommendations = []
    failed_sections = [
        section
        for section in metric_sections
        if any(item["status"] == "FAIL" for item in section["rule_evaluations"])
    ]
    for section in sorted(failed_sections, key=lambda item: item["metric"]["key"]):
        metric = section["metric"]
        if metric["key"] == "02080103" and section["issue_summary"]["confirmed_count"]:
            recommendations.append(
                f"{metric['key']} {metric['name']}未通过规则：先按证据分组审核并去除确认的冗余副本。"
            )
        else:
            recommendations.append(
                f"{metric['key']} {metric['name']}未通过规则：优先核对主测量、阈值和关联证据。"
            )
    for section in sorted(metric_sections, key=lambda item: item["metric"]["key"]):
        if section["status"] == "ERROR":
            metric = section["metric"]
            recommendations.append(
                f"{metric['key']} {metric['name']}执行失败：检查 Calculator 输入、参数和运行日志后重试。"
            )
    unavailable_count = sum(
        item["status"] in {"UNAVAILABLE", "NEEDS_INPUT", "NOT_EVALUATED"}
        for item in coverage_items
    )
    if unavailable_count:
        recommendations.append(
            f"仍有 {unavailable_count} 个指南叶子指标未形成可评分结果；补齐 Calculator、业务输入或评测计划后再提升覆盖率。"
        )

    return QualityReportV1_1.model_validate(
        {
            "schema_version": "1.1",
            "report_id": id_factory(),
            "run_id": run_id,
            "dataset_version_id": dataset_version_id,
            "contract_version_id": contract.contract_version_id,
            "generator": {"key": "qa-eval-report", "version": "1.0.0"},
            "execution_status": execution_status,
            "gate_status": _gate_status(contract, evaluations),
            "coverage": {
                "evaluated": status_counts["OK"],
                "partial": status_counts["PARTIAL"],
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
            "scoring": {
                "evaluated_score": evaluated_score,
                "effective_coverage": effective_coverage,
                "included_metrics": [item.key for item in included],
                "disclaimer": "仅代表已评测指标，不代表指南全指标综合质量",
            },
            "radar": radar,
            "guide_coverage": coverage_items,
            "recommendations": recommendations,
            "generated_at": generated_at,
        }
    )

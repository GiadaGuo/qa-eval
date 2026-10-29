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
    # Weight and mapping version must come from an approved guide/registry.
    # None deliberately means "not formally scoreable"; there is no equal-weight fallback.
    weight: float | None = None
    scoring_mapping_version: str | None = None
    calculator_key: str | None = None
    calculator_version: str | None = None
    execution_mode: str | None = None


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
    if entry.scoring_mapping_version is None:
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
    stage_runs: dict[UUID, dict] | None = None,
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
    observe_only_metrics = {
        key
        for key, item in planned_items.items()
        if item.rule.strategy == "OBSERVE_ONLY"
    }
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
    stage_runs = stage_runs or {}
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
        stage = stage_runs.get(result.stage_run_id, {})
        stage_calculator = stage.get("calculator", {})
        calculator = (
            {"key": stage_calculator["key"], "version": stage_calculator["version"]}
            if stage_calculator.get("key") and stage_calculator.get("version")
            else None
        )
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
                "stage_run_id": result.stage_run_id,
                "calculator": calculator,
                "execution_mode": metadata.execution_mode or stage.get("stage_kind"),
                "population": None
                if result.population is None
                else result.population.model_dump(mode="json"),
                "semantic_coverage": None
                if result.semantic_coverage is None
                else result.semantic_coverage.model_dump(mode="json"),
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
                    (
                        item
                        for item in result.observations
                        if item.key == entry.measurement_key
                    ),
                    None,
                )
                score = (
                    None
                    if observation is None or key in observe_only_metrics
                    else _normalized_score(observation.value, entry)
                )
                status = "EVALUATED"
                reason = (
                    "Metric completed in OBSERVE_ONLY mode and was excluded from scoring"
                    if key in observe_only_metrics
                    else "Metric completed and is scoreable"
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
    weighted_applicable = [item for item in applicable if item.weight is not None]
    raw_weight = sum(item.weight or 0 for item in leaf_catalog)
    denominator = sum(item.weight or 0 for item in weighted_applicable)
    included = [item for item in applicable if (item.key, item.version) in scores]
    included_weight = sum(item.weight or 0 for item in included)
    effective_coverage = 0.0 if denominator == 0 else included_weight / denominator
    partial_score = (
        None
        if included_weight == 0
        else sum(scores[(item.key, item.version)] * (item.weight or 0) for item in included)
        / included_weight
    )
    complete = denominator > 0 and abs(included_weight - denominator) < 1e-12
    overall_score = partial_score if complete else None
    exclusion_reasons: dict[str, list[str]] = {}
    for entry in applicable:
        key = (entry.key, entry.version)
        reasons = []
        result = result_by_metric.get(key)
        if entry.weight is None:
            reasons.append("NO_APPROVED_GUIDE_WEIGHT")
        if entry.scoring_mapping_version is None:
            reasons.append("NO_APPROVED_SCORING_MAPPING")
        if key in observe_only_metrics:
            reasons.append("OBSERVATION_ONLY")
        if result is None:
            reasons.append("NOT_EXECUTED")
        elif result.status == "PARTIAL":
            reasons.append("PARTIAL_COVERAGE")
        elif result.status != "OK":
            reasons.append(result.status)
        elif key not in scores and not reasons:
            reasons.append("MEASUREMENT_NOT_SCOREABLE")
        if reasons:
            exclusion_reasons[entry.key] = reasons

    section_by_key = {
        (item["metric"]["key"], item["metric"]["version"]): item
        for item in metric_sections
    }
    for key, section in section_by_key.items():
        result = result_by_metric[key]
        labels = []
        if key in observe_only_metrics:
            labels.append("OBSERVATION_ONLY")
        if result.status == "PARTIAL":
            labels.append("PARTIAL_COVERAGE")
        elif result.status in {"MISSING", "NOT_APPLICABLE", "ERROR"}:
            labels.append(result.status)
        elif key in scores:
            labels.append("SCOREABLE")
        elif not labels:
            labels.append("OBSERVATION_ONLY")
        section["scoring_eligibility"] = labels
        section["score"] = scores.get(key)
        section["scoring_exclusion_reasons"] = exclusion_reasons.get(key[0], [])

    radar = []
    dimension_scores = []
    for axis in [f"020{i}" for i in range(1, 9)]:
        axis_applicable = [
            item for item in weighted_applicable if _axis_for(item.key) == axis
        ]
        axis_included = [
            item for item in axis_applicable if (item.key, item.version) in scores
        ]
        axis_total = sum(item.weight or 0 for item in axis_applicable)
        axis_weight = sum(item.weight or 0 for item in axis_included)
        axis_coverage = None if axis_total == 0 else axis_weight / axis_total
        axis_complete = axis_total > 0 and abs(axis_weight - axis_total) < 1e-12
        axis_partial_score = None if axis_weight == 0 else sum(
            scores[(item.key, item.version)] * (item.weight or 0)
            for item in axis_included
        ) / axis_weight
        axis_score = axis_partial_score if axis_complete else None
        radar.append(
            {
                "dimension": axis,
                "score": axis_score,
                "coverage": 0.0 if axis_coverage is None else axis_coverage,
            }
        )
        dimension_scores.append(
            {
                "dimension": axis,
                "score": axis_score,
                "effective_weight": axis_weight,
                "applicable_weight": axis_total,
                "coverage": axis_coverage,
                "status": "COMPLETE" if axis_complete else "PARTIAL" if axis_weight else "UNAVAILABLE",
                "reasons": [] if axis_complete else ["DIMENSION_WEIGHT_NOT_FULLY_SCORED"],
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

    recommendation_templates = {
        "EXACT_DUPLICATE_REDUNDANT": "依据重复组证据审核并移除确认的冗余副本。",
        "NEAR_DUPLICATE_CANDIDATE": "复核近重复候选，确认后再执行去重，避免误删。",
        "SOURCE_MISSING": "补齐来源字段和可验证的来源元数据。",
        "STRUCTURE_MISSING": "补齐缺失结构并在入库前增加字段校验。",
        "FIELD_INCONSISTENT": "核对内容生成与同步链路中的字段映射。",
    }
    recommendation_details = []
    seen_recommendations = set()
    for issue in sorted(issues, key=lambda item: (item.source.metric_key, item.code)):
        template = recommendation_templates.get(issue.code)
        if template is None:
            template = "依据关联证据复核该问题，并在确认影响范围后制定修复方案。"
            certainty = "HEURISTIC"
        else:
            certainty = "DETERMINISTIC"
        identity = (issue.source.metric_key, issue.code)
        if identity in seen_recommendations:
            continue
        seen_recommendations.add(identity)
        recommendation_details.append(
            {
                "metric_key": issue.source.metric_key,
                "issue_code": issue.code,
                "certainty": certainty,
                "recommendation": template,
                "basis": f"QualityIssue {issue.code} with evidence for metric {issue.source.metric_key}",
            }
        )

    applicable_count = len(applicable)
    executed_applicable = sum(
        (item.key, item.version) in result_by_metric for item in applicable
    )
    observational_count = sum(
        (item.key, item.version) in result_by_metric
        and (item.key, item.version) not in scores
        and result_by_metric[(item.key, item.version)].status in {"OK", "PARTIAL"}
        for item in applicable
    )

    def ratio_payload(numerator, denominator, basis, unavailable_reason="Denominator is zero"):
        return {
            "numerator": numerator,
            "denominator": denominator,
            "ratio": None if denominator == 0 else numerator / denominator,
            "basis": basis,
            "unavailable_reason": unavailable_reason if denominator == 0 else None,
        }

    data_by_metric = {}
    for entry in applicable:
        key = (entry.key, entry.version)
        result = result_by_metric.get(key)
        label = f"{entry.key}@{entry.version}"
        if result is None or result.population is None:
            data_by_metric[label] = {
                "numerator": None,
                "denominator": None,
                "ratio": None,
                "basis": "MetricResult.population evaluated_count / input_count",
                "unavailable_reason": "Metric was not executed or did not report population",
            }
        else:
            data_by_metric[label] = ratio_payload(
                result.population.evaluated_count,
                result.population.input_count,
                "MetricResult.population evaluated_count / input_count; metrics are not summed",
            )

    coverage_analysis = {
        "implementation": ratio_payload(
            sum(item.guide_status not in {"UNAVAILABLE"} for item in leaf_catalog),
            len(leaf_catalog),
            "Guide leaf metrics with a registered implementation / all guide leaf metrics",
        ),
        "execution": ratio_payload(
            executed_applicable,
            applicable_count,
            "Executed applicable guide leaf metrics / applicable guide leaf metrics",
        ),
        "effective_scoring": ratio_payload(
            included_weight,
            denominator,
            "Formally scoreable weight / applicable weight with explicit guide weights",
            "No applicable metric has an explicit approved guide weight",
        ),
        "observational": ratio_payload(
            observational_count,
            applicable_count,
            "Executed metrics with measurements but without a formal score / applicable guide leaf metrics",
        ),
        "data_by_metric": data_by_metric,
        "applicability_uncertain": any(
            item.guide_status in {"NEEDS_INPUT", "NOT_EVALUATED"} for item in leaf_catalog
        ),
    }

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
                "overall_score": overall_score,
                "overall_score_status": "COMPLETE" if complete else "PARTIAL" if partial_score is not None else "UNAVAILABLE",
                "partial_score": partial_score,
                # Compatibility alias. It is explicitly the partial score, not an overall score.
                "evaluated_score": partial_score,
                "effective_coverage": effective_coverage,
                "raw_weight": raw_weight,
                "applicable_weight": denominator,
                "scored_weight": included_weight,
                "unscored_weight": max(0.0, denominator - included_weight),
                "included_metrics": [item.key for item in included],
                "exclusion_reasons": exclusion_reasons,
                "disclaimer": "仅在适用正式权重全部获得有效评分时生成 Overall Score；partial_score 仅代表已评分子集，不代表指南全指标综合质量。",
            },
            "radar": radar,
            "guide_coverage": coverage_items,
            "recommendations": recommendations,
            "coverage_analysis": coverage_analysis,
            "dimension_scores": dimension_scores,
            "recommendation_details": recommendation_details,
            "generated_at": generated_at,
        }
    )

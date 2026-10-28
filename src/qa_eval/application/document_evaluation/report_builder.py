# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 报告汇总器：把逐指标判定结果汇总为 DocumentQualityReportV1。
# 分数一律由程序复算（SATISFIED 计满、其余计零），不信任 LLM
# 输出中的任何分数（D3：等权平均，任一 ERROR 总分置空）。
# ====================================================

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from qa_eval.application.document_evaluation.evaluator import (
    EvaluationRunResult,
)
from qa_eval.contracts.document import (
    DocumentEvaluatorInfo,
    DocumentEvidence,
    DocumentItemSection,
    DocumentMetricSection,
    DocumentMissingItem,
    DocumentOverallScore,
    DocumentQualityReportV1,
    DocumentRecommendation,
    DocumentSummary,
)
from qa_eval.contracts.metrics import MetricObservation, VersionedKey
from qa_eval.domain.entities.document_version import DocumentVersion

SCORING_POLICY = (
    "每项 SATISFIED 计 100 分，INSUFFICIENT / MISSING / UNVERIFIABLE 计 0 分；"
    "指标得分 = 满足项数 / 检查项数 × 100；"
    "总分 = 各指标得分的等权平均（源指南未定义维度权重，等权为平台冻结决策）；"
    "任一指标 ERROR 时总分置空，不做部分平均。"
)


def build_report(
    *,
    report_id: UUID,
    task_id: UUID,
    document_version: DocumentVersion,
    line_count: int,
    rubric: dict[str, Any],
    rubric_sha256: str,
    result: EvaluationRunResult,
    evaluator: DocumentEvaluatorInfo,
    generated_at: datetime,
) -> DocumentQualityReportV1:
    """由程序复算分数并汇总报告；rubric 提供 item_code → item_name 映射。"""

    item_names = {
        item["item_code"]: item["item_name"]
        for metric in rubric["metrics"]
        for item in metric["items"]
    }
    standard_version = rubric["standard"]["version"]

    sections: list[DocumentMetricSection] = []
    missing_items: list[DocumentMissingItem] = []
    recommendations: list[DocumentRecommendation] = []

    for outcome in result.outcomes:
        if outcome.status == "ERROR":
            sections.append(
                DocumentMetricSection(
                    metric=VersionedKey(
                        key=outcome.metric_code,
                        version=standard_version,
                    ),
                    metric_name=outcome.metric_name,
                    status="ERROR",
                    error=outcome.error,
                )
            )
            continue

        satisfied = sum(1 for item in outcome.items if item["status"] == "SATISFIED")
        required = len(outcome.items)
        score = round(satisfied / required * 100, 2)

        items = [
            DocumentItemSection(
                item_code=item["item_code"],
                item_name=item_names.get(item["item_code"], item["item_code"]),
                status=item["status"],
                document_evidence=[
                    DocumentEvidence.model_validate(evidence)
                    for evidence in item["document_evidence"]
                ],
                inspected_scope=item["inspected_scope"],
                expected_section=item.get("expected_section"),
                reason=item["reason"],
                suggestion=item["suggestion"],
                fixability=item["fixability"],
            )
            for item in outcome.items
        ]

        sections.append(
            DocumentMetricSection(
                metric=VersionedKey(key=outcome.metric_code, version=standard_version),
                metric_name=outcome.metric_name,
                status="OK",
                observations=[
                    MetricObservation(
                        key="satisfied_count",
                        value=satisfied,
                        value_type="NUMBER",
                        numerator=satisfied,
                        denominator=required,
                    ),
                    MetricObservation(
                        key="required_count",
                        value=required,
                        value_type="NUMBER",
                    ),
                    MetricObservation(
                        key="score",
                        value=score,
                        value_type="NUMBER",
                        unit="percent",
                        numerator=satisfied,
                        denominator=required,
                    ),
                ],
                items=items,
            )
        )

        for item in items:
            if item.status == "MISSING":
                missing_items.append(
                    DocumentMissingItem(
                        item_code=item.item_code,
                        item_name=item.item_name,
                        expected_section=item.expected_section or item.inspected_scope,
                    )
                )
            if item.status != "SATISFIED":
                recommendations.append(
                    DocumentRecommendation(
                        item_code=item.item_code,
                        suggestion=item.suggestion,
                        fixability=item.fixability,
                    )
                )

    ok_count = sum(1 for section in sections if section.status == "OK")
    if ok_count == len(sections):
        execution_status = "SUCCEEDED"
    elif ok_count == 0:
        execution_status = "FAILED"
    else:
        execution_status = "PARTIAL_SUCCEEDED"

    if ok_count == len(sections):
        scores = [
            observation.value
            for section in sections
            for observation in section.observations
            if observation.key == "score"
        ]
        overall = DocumentOverallScore(
            score=round(sum(scores) / len(scores), 2),
            scoring_policy=SCORING_POLICY,
        )
    else:
        error_codes = [
            section.metric.key for section in sections if section.status == "ERROR"
        ]
        overall = DocumentOverallScore(
            score=None,
            scoring_policy=SCORING_POLICY,
            explanation=(
                f"指标 {'、'.join(error_codes)} 为 ERROR，总分不可计算，"
                "置空而非按部分平均"
            ),
        )

    return DocumentQualityReportV1(
        report_id=report_id,
        task_id=task_id,
        document_version_id=document_version.id,
        dataset_id=document_version.dataset_id,
        document=DocumentSummary(
            version=document_version.version,
            filename=document_version.filename,
            sha256=document_version.sha256,
            line_count=line_count,
            size_bytes=document_version.size_bytes,
        ),
        evaluation_standard={
            "name": rubric["standard"]["name"],
            "version": standard_version,
            "sha256": rubric_sha256,
        },
        evaluator=evaluator,
        execution_status=execution_status,
        metrics=sections,
        overall=overall,
        missing_items=missing_items,
        recommendations=recommendations,
        limitations=list(result.limitations),
        generated_at=generated_at,
    )

from __future__ import annotations

from typing import Any

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.issues.protocol import IssueBuildContext
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class DatasetCardIssueBuilder:
    key = "dataset-card-issue-builder"
    version = "1.0.0"
    replaces_threshold_summary = False

    def supports(self, result) -> bool:
        return (
            result.metric.key == "BG01_CARD_CONFORMANCE"
            and result.metric.version == "1.0.0"
        )

    def build(self, context: IssueBuildContext) -> list[QualityIssueV1]:
        evidence_items = [
            item for item in context.result.evidence if item.kind == "BG01_CARD_CHECKS"
        ]
        if len(evidence_items) != 1:
            raise ValueError(
                "BG-01 MetricResult must reference one card-check evidence"
            )
        evidence = evidence_items[0]
        if evidence.schema_ref != "qa-eval://schemas/evidence/bg01-card-checks/1.0.0":
            raise ValueError("unsupported BG-01 card-check evidence schema")
        rows = list(EvidenceReader(context.artifact_store).iter_jsonl(evidence))
        if len(rows) != evidence.summary.record_count:
            raise ValueError("BG-01 evidence row count mismatch")
        if evidence.summary.attributes.get("completeness_status") != "COMPLETE":
            raise ValueError(
                "BG-01 evidence must be complete before issue materialization"
            )
        return [
            self._issue(context, evidence.evidence_id, row)
            for row in rows
            if row.get("outcome") in {"MISSING", "PLACEHOLDER", "INVALID"}
        ]

    @staticmethod
    def _issue(context: IssueBuildContext, evidence_id, row: dict[str, Any]):
        result = context.result
        comparison_id = str(row.get("comparison_id", "unknown"))
        outcome = str(row["outcome"])
        measurement = (
            "four_element_completeness_ratio"
            if comparison_id.startswith("element.")
            else "government_field_valid_ratio"
            if outcome == "INVALID"
            else "required_field_completeness_ratio"
        )
        severity = max(
            context.severity_by_item.values(),
            key={"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}.get,
            default="WARNING",
        )
        code = f"CARD_{outcome}"
        return QualityIssueV1.model_validate(
            {
                "issue_id": uuid7(),
                "run_id": result.run_id,
                "dataset_version_id": result.dataset_version_id,
                "source": {
                    "metric_result_id": result.metric_result_id,
                    "metric_key": result.metric.key,
                    "metric_version": result.metric.version,
                    "measurement": measurement,
                    "rule_evaluation_id": None,
                },
                "detector": {
                    "key": "dataset-card-conformance",
                    "version": "1.0.0",
                },
                "category": "DATASET_CARD_CONFORMANCE",
                "code": code,
                "determination": "CONFIRMED",
                "severity": severity,
                "scope": "DATASET",
                "subject": {
                    "type": "DATASET",
                    "dataset_version_id": result.dataset_version_id,
                },
                "message": "数据集卡字段未满足已执行的结构或规则检查。",
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "locator": {
                            "comparison_id": comparison_id,
                            "field_path": row.get("field_path"),
                            "outcome": outcome,
                        },
                    }
                ],
                "dedup_key": (
                    f"{result.metric.key}:{result.metric.version}:"
                    f"{code}:{comparison_id}"
                ),
                "status": "OPEN",
                "created_at": context.created_at,
                "updated_at": context.created_at,
            }
        )

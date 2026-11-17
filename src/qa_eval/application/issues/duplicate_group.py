from __future__ import annotations

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.issues.protocol import IssueBuildContext
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class DuplicateGroupIssueBuilder:
    key = "duplicate-group-issue-builder"
    version = "1.0.0"
    replaces_threshold_summary = False

    def supports(self, result) -> bool:
        return result.metric.key == "02080103"

    def build(self, context: IssueBuildContext) -> list[QualityIssueV1]:
        result = context.result
        matches = [item for item in result.evidence if item.kind == "DUPLICATE_GROUPS"]
        if len(matches) != 1:
            raise ValueError(
                "MetricResult must reference one DUPLICATE_GROUPS evidence"
            )
        evidence = matches[0]
        groups = EvidenceReader(context.artifact_store).read_json(evidence)
        issues: list[QualityIssueV1] = []
        for group in groups["groups"]:
            representative = group["representative_sample_id"]
            for sample_id in group["sample_ids"]:
                if sample_id == representative:
                    continue
                issues.append(
                    self._issue(
                        context,
                        evidence.evidence_id,
                        measurement="exact_redundant_record_ratio",
                        detector="exact_duplicate_redundant_sample",
                        code="EXACT_DUPLICATE_REDUNDANT",
                        determination="CONFIRMED",
                        severity="WARNING",
                        sample_id=sample_id,
                        message="该样本与重复组代表样本的规范化正文完全相同。",
                        locator={
                            "group_id": group["group_id"],
                            "normalized_sha256": group["normalized_sha256"],
                        },
                        related=[
                            {
                                "relation": "DUPLICATE_OF",
                                "subject": {
                                    "type": "SAMPLE",
                                    "sample_id": representative,
                                },
                            }
                        ],
                    )
                )
        for group in groups.get("near_duplicate_groups", []):
            representative = group["sample_ids"][0]
            for sample_id in group["sample_ids"]:
                related = []
                if sample_id != representative:
                    related.append(
                        {
                            "relation": "NEAR_DUPLICATE_GROUP_REPRESENTATIVE",
                            "subject": {
                                "type": "SAMPLE",
                                "sample_id": representative,
                            },
                        }
                    )
                issues.append(
                    self._issue(
                        context,
                        evidence.evidence_id,
                        measurement="near_duplicate_candidate_record_count",
                        detector="near_duplicate_candidate_sample",
                        code="NEAR_DUPLICATE_CANDIDATE",
                        determination="CANDIDATE",
                        severity="INFO",
                        sample_id=sample_id,
                        message="该样本由 MinHash + LSH 召回为近重复候选，尚需复核。",
                        locator={"group_id": group["group_id"]},
                        related=related,
                    )
                )
        return issues

    @staticmethod
    def _issue(
        context,
        evidence_id,
        *,
        measurement,
        detector,
        code,
        determination,
        severity,
        sample_id,
        message,
        locator,
        related,
    ) -> QualityIssueV1:
        result = context.result
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
                "detector": {"key": detector, "version": "1.0.0"},
                "category": "DUPLICATE_CONTENT",
                "code": code,
                "determination": determination,
                "severity": severity,
                "scope": "SAMPLE",
                "subject": {"type": "SAMPLE", "sample_id": sample_id},
                "related_subjects": related,
                "message": message,
                "evidence": [{"evidence_id": evidence_id, "locator": locator}],
                "dedup_key": (
                    f"{result.metric.key}:{code}:{locator['group_id']}:{sample_id}"
                ),
                "status": "OPEN",
                "created_at": context.created_at,
                "updated_at": context.created_at,
            }
        )

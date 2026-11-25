"""Native QualityIssues backed by confirmed within-body contradictions."""

from hashlib import sha256

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class ContentConsistencyIssueBuilder:
    def supports(self, result):
        return (
            result.calculator_key == "content_consistency"
            and result.calculator_version in {"2.0.0", "2.1.0"}
        )

    def build(self, result, evaluation, created_at, artifact_store):
        if not result.statistics.get("contradiction_record_count"):
            return []
        [artifact] = result.decision_artifact_refs
        issues = []
        for row in EvidenceReader(artifact_store).iter_jsonl(artifact):
            if not row["attributes"]["conflict_count"]:
                continue
            sample_hash = sha256(row["sample_id"].encode()).hexdigest()
            pair = next(
                pair
                for pair in row["attributes"]["pairs"]
                if pair["outcome"] == "CONTRADICTION"
            )
            issues.append(
                QualityIssueV1.model_validate(
                    {
                        "issue_id": uuid7(),
                        "run_id": result.run_id,
                        "dataset_version_id": result.dataset_version_id,
                        "source": {
                            "calculator_result_id": result.calculator_result_id,
                            "metric_key": result.metric.key,
                            "metric_version": result.metric.version,
                            "statistic_ref": evaluation.statistic_ref,
                            "rule_evaluation_id": evaluation.rule_evaluation_id,
                            "decision_rule_id": pair["rule_id"],
                            "decision_status": row["status"],
                        },
                        "detector": evaluation.calculator,
                        "category": "CONTENT_CONSISTENCY",
                        "code": "BODY_CONSTRAINT_CONTRADICTION",
                        "determination": "CONFIRMED",
                        "severity": evaluation.severity,
                        "scope": "SAMPLE",
                        "subject": {"type": "SAMPLE", "sample_id": row["sample_id"]},
                        "message": "正文存在适用范围一致且相互冲突的条款。",
                        "evidence": [
                            {
                                "evidence_id": artifact.evidence_id,
                                "locator": {"row_id": row["row_id"]},
                            }
                        ],
                        "dedup_key": f"{result.output_slot_id}:body-consistency:{sample_hash}",
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
            )
        if len(issues) != result.statistics["contradiction_record_count"]:
            raise ValueError(
                "contradiction record count differs from the decision stream"
            )
        return issues

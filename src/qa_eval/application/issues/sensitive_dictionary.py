"""Native QualityIssues backed by record-level sensitive dictionary decisions."""

from hashlib import sha256

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class SensitiveDictionaryIssueBuilder:
    def supports(self, result):
        return (
            result.calculator_key == "sensitive_dictionary_match"
            and result.calculator_version == "1.2.0"
        )

    def build(self, result, evaluation, created_at, artifact_store):
        if not result.statistics.get("non_compliant_record_count"):
            return []
        [artifact] = result.decision_artifact_refs
        issues = []
        for row in EvidenceReader(artifact_store).iter_jsonl(artifact):
            if row["status"] != "0":
                continue
            sample_hash = sha256(row["sample_id"].encode()).hexdigest()
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
                            "decision_rule_id": row["rule_id"],
                            "decision_status": row["status"],
                        },
                        "detector": evaluation.calculator,
                        "category": "SENSITIVE_DICTIONARY",
                        "code": "SENSITIVE_DICTIONARY_THRESHOLD",
                        "determination": "CONFIRMED",
                        "severity": evaluation.severity,
                        "scope": "SAMPLE",
                        "subject": {"type": "SAMPLE", "sample_id": row["sample_id"]},
                        "message": "记录的敏感词命中次数达到记录判定阈值。",
                        "evidence": [
                            {
                                "evidence_id": artifact.evidence_id,
                                "locator": {"row_id": row["row_id"]},
                            }
                        ],
                        "dedup_key": f"{result.output_slot_id}:sensitive-dictionary:{sample_hash}",
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
            )
        if len(issues) != result.statistics["non_compliant_record_count"]:
            raise ValueError(
                "non-compliant record count differs from the decision stream"
            )
        return issues

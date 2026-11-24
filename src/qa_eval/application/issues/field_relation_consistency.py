"""Evidence-backed cross-field conflicts from 0207 cross-field evaluation."""

from hashlib import sha256

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.calculators.field_relation_consistency.calculator import (
    EVIDENCE_KIND,
    KEY,
    VERSION,
)
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class FieldRelationConsistencyIssueBuilder:
    def supports(self, result):
        return (result.calculator_key, result.calculator_version) == (KEY, VERSION)

    def build(self, result, evaluation, created_at, artifact_store):
        expected = result.statistics.get("confirmed_conflict_count", 0)
        if not expected:
            return []
        [artifact] = result.decision_artifact_refs
        if artifact.kind != EVIDENCE_KIND:
            raise ValueError("unexpected 0207 decision artifact kind")
        issues = []
        for row in EvidenceReader(artifact_store).iter_jsonl(artifact):
            fields = row["attributes"]["fields"]
            for edge in row["attributes"]["relations"]:
                if not edge["confirmed_conflict"]:
                    continue
                digest = sha256(row["sample_id"].encode()).hexdigest()
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
                                "decision_rule_id": edge["id"],
                                "decision_status": row["status"],
                            },
                            "detector": evaluation.calculator,
                            "category": "FIELD_CONSISTENCY",
                            "code": "FIELD_VALUES_CONTRADICT",
                            "determination": "CONFIRMED",
                            "severity": evaluation.severity,
                            "scope": "SAMPLE",
                            "subject": {
                                "type": "SAMPLE",
                                "sample_id": row["sample_id"],
                            },
                            "message": "同一记录中可比字段的值存在明确冲突。",
                            "evidence": [
                                {
                                    "evidence_id": artifact.evidence_id,
                                    "locator": {
                                        "row_id": row["row_id"],
                                        "relation_id": edge["id"],
                                        "method": edge["method"],
                                        "raw_score": edge["raw_score"],
                                        "score": edge["score"],
                                        "reason": edge["reason"],
                                        "left": fields[edge["left"]],
                                        "right": fields[edge["right"]],
                                        "details": edge["details"],
                                    },
                                }
                            ],
                            "dedup_key": f"{result.output_slot_id}:field-consistency:{digest}:{edge['id']}",
                            "status": "OPEN",
                            "created_at": created_at,
                            "updated_at": created_at,
                        }
                    )
                )
        if len(issues) != expected:
            raise ValueError("0207 conflict count differs from its decision evidence")
        return issues

from hashlib import sha256

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)
from qa_eval.contracts.quality import IssueEvidenceRef, QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class PolicyEffectivenessIssueBuilder:
    """Materialize policy-specific candidates from the Calculator's decision stream."""

    def supports(self, result):
        return (result.calculator_key, result.calculator_version) == (
            "policy_effectiveness",
            "1.0.0",
        )

    def build(self, result, evaluation, created_at, artifact_store):
        if result.status != "OK" or result.status_detail.get("partial_scope") is True:
            return []
        if (
            result.output_schema.decision_retention != "ALL"
            or len(result.decision_artifact_refs) != 1
        ):
            raise ValueError("policy issues require the complete decision artifact")
        artifact = result.decision_artifact_refs[0]
        if artifact.schema_ref != DECISION_SCHEMA_REF:
            raise ValueError("unsupported policy decision schema")
        artifact_key = artifact.summary.attributes["artifact_key"]
        issues = {}
        for row in EvidenceReader(artifact_store).iter_jsonl(artifact):
            decision = CalculatorDecisionV1.model_validate(row)
            if (
                decision.status != "EXPIRED_EVIDENCED"
                or decision.attributes.get("applicability") != "APPLICABLE"
            ):
                continue
            if not any(
                loc.attributes.get("target") == "SELF" for loc in decision.locations
            ):
                raise ValueError(
                    "policy expiration decision lacks evidence of its own instrument"
                )
            if not any(
                ref.artifact_key == artifact_key and ref.row_id == decision.row_id
                for ref in decision.evidence_refs
            ):
                raise ValueError(
                    "policy expiration decision lacks a resolvable evidence reference"
                )
            issue = issues.get(decision.sample_id)
            evidence = {
                "evidence_id": artifact.evidence_id,
                "locator": {"row_id": decision.row_id},
            }
            if issue is not None:
                if not any(
                    ref.locator == evidence["locator"] for ref in issue.evidence
                ):
                    issue.evidence.append(IssueEvidenceRef.model_validate(evidence))
                continue
            sample_hash = sha256(decision.sample_id.encode()).hexdigest()
            issues[decision.sample_id] = QualityIssueV1.model_validate(
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
                        "decision_rule_id": decision.rule_id,
                        "decision_status": decision.status,
                    },
                    "detector": evaluation.calculator,
                    "category": "POLICY_EFFECTIVENESS",
                    "code": "POLICY_SELF_EXPIRATION_EVIDENCED",
                    "determination": "SUSPECTED",
                    "severity": evaluation.severity,
                    "scope": "SAMPLE",
                    "subject": {"type": "SAMPLE", "sample_id": decision.sample_id},
                    "message": "正文或标题存在指向本文件的失效证据，待复核。",
                    "evidence": [evidence],
                    "dedup_key": f"{result.output_slot_id}:policy-expiration:{sample_hash}",
                    "status": "OPEN",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
        if len(issues) != result.statistics.get("expired_evidenced_count"):
            raise ValueError(
                "policy expiry population differs from its decision stream"
            )
        return list(issues.values())

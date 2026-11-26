"""Issue strategy for record-level format failures and normative evidence."""

from hashlib import sha256

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7


class PolicyDocumentFormatIssueBuilder:
    def supports(self, result):
        return (
            result.calculator_key == "policy_document_format"
            and result.calculator_version
            in {
                "1.0.0",
                "1.1.0",
            }
        )

    def build(self, result, evaluation, created_at, artifact_store):
        if result.status != "OK" or not (
            result.statistics["normative_violation_count"]
            or result.statistics["non_compliant_record_count"]
        ):
            return []
        [artifact] = result.decision_artifact_refs
        if (
            result.output_schema.decision_retention != "ALL"
            or artifact.schema_ref != DECISION_SCHEMA_REF
        ):
            raise ValueError(
                "format issues require the complete native decision stream"
            )
        artifact_key = artifact.summary.attributes["artifact_key"]
        issues = {}
        normative_samples = set()
        for row in EvidenceReader(artifact_store).iter_jsonl(artifact):
            decision = CalculatorDecisionV1.model_validate(row)
            rule = decision.attributes.get("normative_violation")
            if not rule and decision.status != "0":
                continue
            if (
                rule
                and not any(
                    loc.attributes.get("rule_id") == rule for loc in decision.locations
                )
            ) or not any(
                ref.artifact_key == artifact_key and ref.row_id == decision.row_id
                for ref in decision.evidence_refs
            ):
                raise ValueError("format violation lacks resolvable source evidence")
            if rule:
                normative_samples.add(decision.sample_id)
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
                        "decision_rule_id": rule or decision.rule_id,
                        "decision_status": decision.status,
                    },
                    "detector": evaluation.calculator,
                    "category": "POLICY_DOCUMENT_FORMAT",
                    "code": (
                        "POLICY_FORMAT_NORMATIVE_DEFECT"
                        if rule
                        else "POLICY_FORMAT_SCORE_BELOW_THRESHOLD"
                    ),
                    "determination": "SUSPECTED",
                    "severity": evaluation.severity,
                    "scope": "SAMPLE",
                    "subject": {"type": "SAMPLE", "sample_id": decision.sample_id},
                    "message": (
                        "保真原文中存在明确格式反证，待复核。"
                        if rule
                        else "必评段得分上界低于合规阈值，详见段落判定及原文证据。"
                    ),
                    "evidence": [
                        {
                            "evidence_id": artifact.evidence_id,
                            "locator": {"row_id": decision.row_id},
                        }
                    ],
                    "dedup_key": f"{result.output_slot_id}:policy-format:{sample_hash}",
                    "status": "OPEN",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
        if len(normative_samples) != result.statistics["normative_violation_count"]:
            raise ValueError(
                "normative violation population differs from the decision stream"
            )
        return list(issues.values())

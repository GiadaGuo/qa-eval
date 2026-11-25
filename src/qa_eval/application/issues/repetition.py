"""Generate a cluster issue or a record-location issue from repetition evidence."""

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7

CROSS = "CROSS_RECORD_DUPLICATE"
INTRA = "INTRA_RECORD_REPETITION"


class RepetitionIssueBuilder:
    key = "repetition-issue-builder"
    version = "1.0.0"
    replaces_threshold_summary = False

    def supports(self, result):
        return any(item.kind in {CROSS, INTRA} for item in result.evidence)

    def build(self, context):
        result = context.result
        issues = []
        seen_groups = set()
        for artifact in result.evidence:
            if artifact.kind not in {CROSS, INTRA}:
                continue
            for row_index, row in enumerate(
                EvidenceReader(context.artifact_store).iter_jsonl(artifact)
            ):
                if artifact.kind == CROSS:
                    if (
                        "member_count" in row
                        and row["sample_id"] != row["representative_sample_id"]
                    ):
                        continue
                    if row["group_id"] in seen_groups:
                        continue
                    seen_groups.add(row["group_id"])
                    subject = {
                        "type": "GROUP",
                        "group_id": row["group_id"],
                        "member_count": row.get("member_count", 2),
                    }
                    measurement = "duplicate_redundant_record_ratio"
                    determination = row["determination"]
                    message = "跨记录重复组或相似记录对。"
                else:
                    if not row["locations"]:
                        continue
                    subject = {"type": "SAMPLE", "sample_id": row["sample_id"]}
                    measurement = "intra_record_repetition_score"
                    determination = "CONFIRMED"
                    message = "记录正文中出现重复片段。"
                issues.append(
                    QualityIssueV1.model_validate(
                        {
                            "issue_id": uuid7(),
                            "run_id": result.run_id,
                            "dataset_version_id": result.dataset_version_id,
                            "source": {
                                "metric_result_id": result.metric_result_id,
                                "metric_key": result.metric.key,
                                "metric_version": result.metric.version,
                                "measurement": measurement,
                            },
                            "detector": {"key": self.key, "version": self.version},
                            "category": "DUPLICATE_CONTENT",
                            "code": artifact.kind,
                            "determination": determination,
                            "severity": "INFO"
                            if determination == "CANDIDATE"
                            else "WARNING",
                            "scope": subject["type"],
                            "subject": subject,
                            "message": message,
                            "evidence": [
                                {
                                    "evidence_id": artifact.evidence_id,
                                    "locator": {**row},
                                }
                            ],
                            "dedup_key": f"{result.metric_result_id}:{artifact.kind}:{row_index}",
                            "status": "OPEN",
                            "created_at": context.created_at,
                            "updated_at": context.created_at,
                        }
                    )
                )
        return issues

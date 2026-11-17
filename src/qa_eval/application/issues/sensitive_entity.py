from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.issues.protocol import IssueBuildContext
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.uuid7 import uuid7

_SUPPORTED_SCHEMAS = {
    "qa-eval://schemas/evidence/sensitive-entity-hits/1.0.0",
    "qa-eval://schemas/evidence/sensitive-entity-hits/1.1.0",
}
_SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}
_CATEGORY_LABELS = {
    "MOBILE_PHONE_CN": "手机号",
    "PRC_RESIDENT_ID": "居民身份证号",
    "CASE_INVOLVED_PERSON": "涉案人员信息",
    "INTERNAL_INSTRUCTION": "内部指令",
}


class SensitiveEntityIssueBuilder:
    key = "sensitive-entity-issue-builder"
    version = "1.0.0"
    replaces_threshold_summary = True

    def supports(self, result) -> bool:
        return result.metric.key == "0202" and result.metric.version == "2.0.0"

    def build(self, context: IssueBuildContext) -> list[QualityIssueV1]:
        matches = [
            item
            for item in context.result.evidence
            if item.kind == "SENSITIVE_ENTITY_HITS"
        ]
        if not matches:
            return []
        if len(matches) != 1:
            raise ValueError(
                "MetricResult must reference at most one SENSITIVE_ENTITY_HITS evidence"
            )
        evidence = matches[0]
        if evidence.schema_ref not in _SUPPORTED_SCHEMAS:
            raise ValueError(
                f"unsupported SENSITIVE_ENTITY_HITS schema: {evidence.schema_ref}"
            )

        completeness = self._completeness(evidence)
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        row_count = 0
        for raw in EvidenceReader(context.artifact_store).iter_jsonl(evidence):
            row = self._validate_row(raw, evidence.schema_ref)
            row_count += 1
            if row["review_status"] == "REJECTED":
                continue
            groups[(row["sample_id"], row["content_id"], row["category"])].append(row)
        self._validate_completeness(evidence, completeness, row_count)

        severity = self._severity(context)
        issues = [
            self._issue(
                context,
                evidence.evidence_id,
                completeness,
                severity,
                key,
                rows,
            )
            for key, rows in sorted(groups.items())
        ]
        return issues

    @staticmethod
    def _completeness(evidence) -> str:
        value = evidence.summary.attributes.get("completeness_status", "UNKNOWN")
        if value not in {"COMPLETE", "INCOMPLETE", "UNKNOWN"}:
            raise ValueError("invalid sensitive entity evidence completeness status")
        return value

    @staticmethod
    def _validate_completeness(evidence, completeness: str, row_count: int) -> None:
        attributes = evidence.summary.attributes
        if row_count != evidence.summary.record_count:
            raise ValueError("sensitive entity Evidence row count mismatch")
        if completeness != "COMPLETE":
            return
        detected = attributes.get("detected_count")
        emitted = attributes.get("emitted_count")
        if (
            attributes.get("truncated") is not False
            or detected != emitted
            or emitted != row_count
        ):
            raise ValueError("COMPLETE sensitive entity Evidence counts do not close")

    @staticmethod
    def _validate_row(row: dict[str, Any], schema_ref: str) -> dict[str, Any]:
        required_strings = (
            "sample_id",
            "content_id",
            "logical_field",
            "category",
            "coordinate_space",
            "mapping_precision",
            "review_status",
        )
        if any(
            not isinstance(row.get(key), str) or not row[key]
            for key in required_strings
        ):
            raise ValueError(
                "sensitive entity Evidence row has invalid identity fields"
            )
        start, end = row.get("start"), row.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise ValueError("sensitive entity Evidence row has invalid span")
        channels = row.get("channels")
        if not isinstance(channels, list) or not channels:
            raise ValueError("sensitive entity Evidence row requires channels")
        if not isinstance(row.get("rule_ids"), list):
            raise TypeError("sensitive entity Evidence row requires rule_ids")

        normalized = dict(row)
        normalized.setdefault("offset_unit", "UNICODE_CODE_POINT")
        normalized.setdefault(
            "field_path",
            f"$.contents[content_id={row['content_id']}].payload.text",
        )
        normalized.setdefault("text_version", None)
        normalized.setdefault("row_id", _legacy_row_id(schema_ref, row))
        if not _is_sha256(normalized["row_id"]):
            raise ValueError("sensitive entity Evidence row_id is invalid")
        if normalized["text_version"] is not None and not _is_sha256(
            normalized["text_version"]
        ):
            raise ValueError("sensitive entity Evidence text_version is invalid")
        return normalized

    @staticmethod
    def _severity(context: IssueBuildContext) -> str:
        severities = [
            context.severity_by_item[item.contract_item_key]
            for item in context.evaluations
            if item.contract_item_key in context.severity_by_item
        ]
        return max(severities, key=_SEVERITY_RANK.__getitem__, default="WARNING")

    def _issue(
        self,
        context: IssueBuildContext,
        evidence_id,
        completeness: str,
        severity: str,
        key: tuple[str, str, str],
        rows: list[dict[str, Any]],
    ) -> QualityIssueV1:
        sample_id, content_id, category = key
        confirmed = all(row["review_status"] == "CONFIRMED" for row in rows)
        determination = "CONFIRMED" if confirmed else "CANDIDATE"
        label = _CATEGORY_LABELS.get(category, category)
        failed = next(
            (item for item in context.evaluations if item.status == "FAIL"), None
        )
        return QualityIssueV1.model_validate(
            {
                "issue_id": uuid7(),
                "run_id": context.result.run_id,
                "dataset_version_id": context.result.dataset_version_id,
                "source": {
                    "metric_result_id": context.result.metric_result_id,
                    "metric_key": context.result.metric.key,
                    "metric_version": context.result.metric.version,
                    "measurement": "sensitive_entity_hit_count",
                    "rule_evaluation_id": None
                    if failed is None
                    else failed.rule_evaluation_id,
                },
                "detector": {"key": self.key, "version": self.version},
                "category": "SENSITIVE_ENTITY",
                "code": f"SENSITIVE_ENTITY_{category}",
                "determination": determination,
                "severity": severity,
                "scope": "CONTENT",
                "subject": {
                    "type": "CONTENT",
                    "sample_id": sample_id,
                    "content_id": content_id,
                },
                "message": f"该内容疑似包含{label}，请复核。",
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "locator": {
                            "type": "EVIDENCE_ROW",
                            "version": "1.0",
                            "row_id": row["row_id"],
                            "field_path": row["field_path"],
                            "start": row["start"],
                            "end": row["end"],
                            "offset_unit": row["offset_unit"],
                            "coordinate_space": row["coordinate_space"],
                            "text_version": row["text_version"],
                            "mapping_precision": row["mapping_precision"],
                            "evidence_completeness": completeness,
                        },
                    }
                    for row in sorted(rows, key=lambda item: item["row_id"])
                ],
                "dedup_key": (
                    f"{context.result.metric.key}:{context.result.metric.version}:"
                    f"SENSITIVE_ENTITY:{sample_id}:{content_id}:{category}"
                ),
                "status": "OPEN",
                "created_at": context.created_at,
                "updated_at": context.created_at,
            }
        )


def _legacy_row_id(schema_ref: str, row: dict[str, Any]) -> str:
    identity = {
        "schema_ref": schema_ref,
        "sample_id": row.get("sample_id"),
        "content_id": row.get("content_id"),
        "category": row.get("category"),
        "start": row.get("start"),
        "end": row.get("end"),
        "coordinate_space": row.get("coordinate_space"),
        "channels": row.get("channels"),
        "rule_ids": row.get("rule_ids"),
        "model_id": row.get("model_id"),
    }
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )

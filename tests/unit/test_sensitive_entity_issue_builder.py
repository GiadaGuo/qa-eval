import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qa_eval.application.issues.protocol import IssueBuildContext
from qa_eval.application.issues.sensitive_entity import SensitiveEntityIssueBuilder
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _row(row_id: str, start: int, end: int, masked: str) -> dict:
    return {
        "row_id": f"sha256:{row_id * 64}",
        "sample_id": "sample-1",
        "content_id": "body-plain",
        "field_path": "$.contents[content_id=body-plain].payload.text",
        "logical_field": "article.body",
        "category": "MOBILE_PHONE_CN",
        "start": start,
        "end": end,
        "offset_unit": "UNICODE_CODE_POINT",
        "coordinate_space": "CONTENT_TEXT_UNICODE_CODE_POINT",
        "text_version": f"sha256:{'f' * 64}",
        "mapping_precision": "EXACT",
        "channels": ["RULE"],
        "masked_excerpt": masked,
        "model_id": None,
        "model_score": None,
        "model_original_label": None,
        "rule_ids": ["mobile-cn-v1"],
        "review_status": "UNREVIEWED",
    }


def _context(
    tmp_path,
    rows,
    *,
    completeness="COMPLETE",
    detected=None,
    schema_ref="qa-eval://schemas/evidence/sensitive-entity-hits/1.1.0",
):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    relative = "evidence/hits.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    stored = store.commit(relative)
    version_id = uuid4()
    result = MetricResultV1.model_validate(
        {
            "metric_result_id": uuid4(),
            "run_id": uuid4(),
            "stage_run_id": uuid4(),
            "dataset_version_id": version_id,
            "metric": {"key": "0202", "version": "2.0.0"},
            "subject": {"scope": "DATASET", "id": str(version_id)},
            "status": "OK",
            "observations": [
                {
                    "key": "sensitive_entity_hit_count",
                    "value": len(rows),
                    "value_type": "NUMBER",
                    "unit": "entity",
                }
            ],
            "population": {
                "input_count": 1,
                "evaluated_count": 1,
                "excluded_count": 0,
                "exclusions": [],
            },
            "evidence": [
                {
                    "evidence_id": uuid4(),
                    "kind": "SENSITIVE_ENTITY_HITS",
                    "schema_ref": schema_ref,
                    "artifact": {
                        "artifact_id": uuid4(),
                        "kind": "METRIC_EVIDENCE",
                        "uri": stored.uri,
                        "format": "jsonl",
                        "media_type": "application/x-ndjson",
                        "schema_ref": schema_ref,
                        "size_bytes": stored.size_bytes,
                        "record_count": len(rows),
                        "checksum": {"algorithm": "sha256", "value": stored.sha256},
                    },
                    "subject_scope": "CONTENT",
                    "summary": {
                        "record_count": len(rows),
                        "attributes": {
                            "detected_count": len(rows)
                            if detected is None
                            else detected,
                            "emitted_count": len(rows),
                            "truncated": completeness == "INCOMPLETE",
                            "completeness_status": completeness,
                        },
                    },
                    "redaction": {
                        "contains_raw_content": False,
                        "policy": "category-mask-no-raw-sensitive-value",
                    },
                }
            ],
            "created_at": NOW,
        }
    )
    return IssueBuildContext(
        result=result,
        evaluations=(),
        severity_by_item={},
        created_at=NOW,
        artifact_store=store,
    )


def test_builder_groups_multiple_spans_into_one_content_candidate(tmp_path):
    rows = [
        _row("a", 3, 14, "138****8000"),
        _row("b", 20, 31, "139****5678"),
    ]
    issues = SensitiveEntityIssueBuilder().build(_context(tmp_path, rows))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.scope == "CONTENT"
    assert issue.subject.sample_id == "sample-1"
    assert issue.subject.content_id == "body-plain"
    assert issue.determination == "CANDIDATE"
    assert issue.code == "SENSITIVE_ENTITY_MOBILE_PHONE_CN"
    assert len(issue.evidence) == 2
    assert {item.locator["row_id"] for item in issue.evidence} == {
        f"sha256:{'a' * 64}",
        f"sha256:{'b' * 64}",
    }
    assert all(
        item.locator["evidence_completeness"] == "COMPLETE" for item in issue.evidence
    )


def test_builder_rejects_false_complete_counts(tmp_path):
    context = _context(
        tmp_path,
        [_row("a", 3, 14, "138****8000")],
        detected=2,
    )
    with pytest.raises(ValueError, match="counts do not close"):
        SensitiveEntityIssueBuilder().build(context)


def test_builder_allows_historical_unknown_completeness(tmp_path):
    row = _row("a", 3, 14, "138****8000")
    row.pop("row_id")
    row.pop("text_version")
    context = _context(
        tmp_path,
        [row],
        completeness="UNKNOWN",
        schema_ref="qa-eval://schemas/evidence/sensitive-entity-hits/1.0.0",
    )
    issues = SensitiveEntityIssueBuilder().build(context)
    assert len(issues) == 1
    assert issues[0].evidence[0].locator["evidence_completeness"] == "UNKNOWN"
    assert issues[0].evidence[0].locator["text_version"] is None

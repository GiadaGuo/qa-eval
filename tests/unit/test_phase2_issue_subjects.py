from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qa_eval.contracts.quality import QualityIssueV1

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _issue(subject, related_subjects):
    scope = subject["type"]
    return QualityIssueV1.model_validate(
        {
            "issue_id": uuid4(),
            "run_id": uuid4(),
            "dataset_version_id": uuid4(),
            "source": {
                "metric_result_id": uuid4(),
                "metric_key": "0208",
                "metric_version": "1.0.0",
                "measurement": "duplicate_ratio",
            },
            "detector": {"key": "phase2-acceptance", "version": "1.0.0"},
            "category": "DUPLICATE_CONTENT",
            "code": "DUPLICATE_CLUSTER",
            "determination": "CONFIRMED",
            "severity": "WARNING",
            "scope": scope,
            "subject": subject,
            "related_subjects": related_subjects,
            "message": "可追溯的问题",
            "evidence": [
                {"evidence_id": uuid4(), "locator": {"row_id": "row-left"}},
                {"evidence_id": uuid4(), "locator": {"row_id": "row-right"}},
            ],
            "dedup_key": f"{scope}:stable",
            "status": "OPEN",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


@pytest.mark.parametrize(
    ("subject", "related"),
    [
        (
            {
                "type": "PAIR",
                "pair_id": "a--b",
                "left_sample_id": "a",
                "right_sample_id": "b",
            },
            [
                {"relation": "LEFT", "subject": {"type": "SAMPLE", "sample_id": "a"}},
                {"relation": "RIGHT", "subject": {"type": "SAMPLE", "sample_id": "b"}},
            ],
        ),
        (
            {"type": "GROUP", "group_id": "sha256:cluster", "member_count": 2},
            [
                {"relation": "MEMBER", "subject": {"type": "SAMPLE", "sample_id": "a"}},
                {"relation": "MEMBER", "subject": {"type": "SAMPLE", "sample_id": "b"}},
            ],
        ),
    ],
)
def test_pair_and_group_keep_all_related_subjects_and_evidence(subject, related):
    issue = _issue(subject, related)
    assert len(issue.related_subjects) == 2
    assert len(issue.evidence) == 2
    assert issue.scope == issue.subject.type


def test_pair_identity_is_order_sensitive_at_contract_boundary():
    issue = _issue(
        {
            "type": "PAIR",
            "pair_id": "a--b",
            "left_sample_id": "a",
            "right_sample_id": "b",
        },
        [],
    )
    serialized = issue.model_dump(mode="json")
    assert serialized["subject"]["left_sample_id"] == "a"
    assert serialized["subject"]["right_sample_id"] == "b"

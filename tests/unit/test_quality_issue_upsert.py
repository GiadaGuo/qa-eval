from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.quality_result import QualityIssueModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _issue(*, run_id, result_id, dedup_key, status="OPEN") -> QualityIssueV1:
    return QualityIssueV1.model_validate(
        {
            "issue_id": uuid4(),
            "run_id": run_id,
            "dataset_version_id": uuid4(),
            "source": {
                "metric_result_id": result_id,
                "metric_key": "0202",
                "metric_version": "2.0.0",
                "measurement": "sensitive_entity_hit_count",
            },
            "detector": {"key": "test-builder", "version": "1.0.0"},
            "category": "TEST",
            "code": "TEST_ISSUE",
            "determination": "CANDIDATE",
            "severity": "WARNING",
            "scope": "DATASET",
            "subject": {"type": "DATASET", "dataset_version_id": uuid4()},
            "message": "candidate",
            "evidence": [{"evidence_id": uuid4(), "locator": {"measurement": "count"}}],
            "dedup_key": dedup_key,
            "status": status,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def test_issue_upsert_is_idempotent_and_preserves_managed_state(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    run_id, result_id = uuid4(), uuid4()
    first = _issue(run_id=run_id, result_id=result_id, dedup_key="stable-key")
    second = _issue(run_id=run_id, result_id=result_id, dedup_key="stable-key")

    with Session(engine) as session:
        service = QualityResultService(
            session, LocalFileSystemArtifactStore(tmp_path / "artifacts")
        )
        persisted = service._upsert_issues([first])
        session.flush()
        original_id = persisted[0].issue_id

        row = session.scalar(select(QualityIssueModel))
        assert row is not None
        document = dict(row.document)
        document["status"] = "ACKNOWLEDGED"
        row.document = document
        session.flush()

        repeated = service._upsert_issues([second])
        session.flush()

        assert session.scalar(select(func.count()).select_from(QualityIssueModel)) == 1
        assert repeated[0].issue_id == original_id
        assert repeated[0].status == "ACKNOWLEDGED"
        assert repeated[0].created_at == first.created_at


def test_issue_upsert_build_failure_writes_nothing(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = QualityResultService(
            session, LocalFileSystemArtifactStore(tmp_path / "artifacts")
        )
        # Candidate materialization happens only after all Builders return;
        # an empty candidate set is a no-op and cannot expose partial rows.
        assert service._upsert_issues([]) == []
        assert session.scalar(select(func.count()).select_from(QualityIssueModel)) == 0

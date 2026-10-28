from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel


def _proposal(version_id, profile_fingerprint):
    return {
        "schema_version": "1.0",
        "proposal_id": str(uuid4()),
        "revision": 1,
        "dataset_version_id": str(version_id),
        "profile_fingerprint": profile_fingerprint,
        "created_by": "USER",
        "created_at": datetime.now(UTC).isoformat(),
        "items": [
            {
                "key": "duplicate",
                "metric": {
                    "key": "02080103",
                    "version": "1.0.0",
                    "measurement": "exact_affected_record_ratio",
                },
                "candidates": [
                    {"key": "exact_text_duplicate", "version": "1.0.0"},
                    {"key": "text_duplicate_degree", "version": "2.0.0"},
                ],
                "selected_calculator": {
                    "key": "exact_text_duplicate",
                    "version": "1.0.0",
                },
                "parameters": {"content_role": "article.body.plain"},
                "rule": {
                    "operator": "LTE",
                    "threshold": 0.5,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        ],
    }


def test_proposal_validation_uses_current_profile_and_registry():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id, version_id = uuid4(), uuid4()
    checksum = "a" * 64
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                artifact_refs={
                    "manifest": {"uri": "manifest.json"},
                    "normalized": {"uri": "normalized.jsonl"},
                    "field_mapping": {
                        "mappings": [
                            {
                                "action": "MAP",
                                "target": {
                                    "kind": "CONTENT",
                                    "role": "article.body.plain",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.add(
            DatasetProfileModel(
                id=uuid4(),
                dataset_version_id=version_id,
                document={"structure": {"content_roles": ["article.body.plain"]}},
                checksum_sha256=checksum,
                profiler_key="base",
                profiler_version="1.0.0",
                config_fingerprint=f"sha256:{'b' * 64}",
            )
        )
        MetricRegistryService(session).sync()
        session.commit()

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        valid = client.post(
            "/api/v1/evaluation-proposals/validate",
            json=_proposal(version_id, f"sha256:{checksum}"),
        )
        stale_payload = _proposal(version_id, f"sha256:{'c' * 64}")
        stale = client.post("/api/v1/evaluation-proposals/validate", json=stale_payload)
        unregistered_payload = _proposal(version_id, f"sha256:{checksum}")
        unregistered_payload["items"][0]["selected_calculator"] = {
            "key": "missing",
            "version": "1.0.0",
        }
        unregistered_payload["items"][0]["candidates"].append(
            {"key": "missing", "version": "1.0.0"}
        )
        unregistered = client.post(
            "/api/v1/evaluation-proposals/validate", json=unregistered_payload
        )

    assert valid.status_code == 200
    assert valid.json()["valid"] is True
    assert valid.json()["items"][0]["selected_applicability"]["status"] == "APPLICABLE"
    assert len(valid.json()["items"][0]["candidates"]) == 2
    assert stale.json()["valid"] is False
    assert stale.json()["findings"][0]["code"] == "PROFILE_FINGERPRINT_MISMATCH"
    assert unregistered.json()["valid"] is False
    assert (
        unregistered.json()["items"][0]["selected_applicability"]["status"]
        == "UNAVAILABLE"
    )

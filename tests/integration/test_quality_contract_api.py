from collections.abc import Generator
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel


def _payload(dataset_version_id: str) -> dict:
    return {
        "name": "重复内容质量契约",
        "version_label": "v1.0.0",
        "dataset_version_id": dataset_version_id,
        "metric_key": "02080103",
        "metric_version": "1.0.0",
        "measurement": "exact_affected_record_ratio",
        "calculator_key": "text_duplicate_degree",
        "calculator_version": "2.0.0",
        "parameters": {
            "content_role": "article.body.plain",
            "char_ngram_size": 3,
            "near_duplicate_threshold": 0.8,
        },
        "rule": {
            "operator": "LTE",
            "threshold": 0.01,
            "severity": "ERROR",
            "on_missing": "UNKNOWN",
        },
    }


def test_publish_quality_contract_validates_and_persists() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dataset_id = uuid4()
    version_id = uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                artifact_refs={
                    "manifest": {"uri": "datasets/manifest.json"},
                    "field_mapping": {
                        "mappings": [
                            {
                                "source_path": "$.text",
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
        session.commit()

    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        published = client.post(
            "/api/v1/quality-contract-versions", json=_payload(str(version_id))
        )
        listed = client.get("/api/v1/quality-contract-versions")
        invalid = _payload(str(version_id))
        invalid["parameters"]["char_ngram_size"] = 0
        rejected = client.post("/api/v1/quality-contract-versions", json=invalid)

    assert published.status_code == 201
    body = published.json()
    assert body["target_dataset_version_id"] == str(version_id)
    assert body["contract"]["status"] == "PUBLISHED"
    assert body["contract"]["fingerprint"].startswith("sha256:")
    assert body["contract"]["items"][0]["metric"]["measurement"] == (
        "exact_affected_record_ratio"
    )
    assert len(listed.json()) == 1
    assert rejected.status_code == 422
    assert "below minimum" in rejected.json()["detail"]

import json
from collections.abc import Generator
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.application.services.logical_planner_service import LogicalPlannerService
from qa_eval.application.services.quality_contract_service import QualityContractService
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
                    "normalized": {"uri": "datasets/normalized.jsonl"},
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
        synced = client.post("/api/v1/registry/sync")
        published = client.post(
            "/api/v1/quality-contract-versions", json=_payload(str(version_id))
        )
        listed = client.get("/api/v1/quality-contract-versions")
        invalid = _payload(str(version_id))
        invalid["parameters"]["char_ngram_size"] = 0
        rejected = client.post("/api/v1/quality-contract-versions", json=invalid)
        multi = client.post(
            "/api/v1/quality-contract-versions/multi",
            json={
                "name": "综合质量契约",
                "version_label": "v2.0.0",
                "dataset_version_id": str(version_id),
                "items": [
                    {
                        "key": "completeness",
                        "metric_key": "0204",
                        "metric_version": "1.0.0",
                        "measurement": "complete_record_ratio",
                        "calculator_key": "structural_completeness",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": {
                            "operator": "GTE",
                            "threshold": 0.9,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    },
                    {
                        "key": "duplicate",
                        "metric_key": "02080103",
                        "metric_version": "1.0.0",
                        "measurement": "exact_affected_record_ratio",
                        "calculator_key": "exact_text_duplicate",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.1,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    },
                ],
            },
        )
        invalid_multi_json = json.loads(multi.request.content)
        invalid_multi_json["version_label"] = "v3.0.0"
        invalid_multi_json["items"][1]["calculator_key"] = "missing"
        invalid_multi = client.post(
            "/api/v1/quality-contract-versions/multi", json=invalid_multi_json
        )
        listed_after_multi = client.get("/api/v1/quality-contract-versions")
        manual = {
            "name": "人工质量契约",
            "version_label": "manual-v1",
            "dataset_version_id": str(version_id),
            "excluded_metrics": {f"020{i}": "小样本验收范围" for i in range(1, 8)},
            "items": [
                {
                    "key": key,
                    "metric_key": "0208",
                    "metric_version": "2.0.0",
                    "calculator_key": key,
                    "calculator_version": "1.0.0",
                    "measurement": measurement,
                    "parameters": parameters,
                    "rule": {"operator": "LTE", "threshold": threshold, "severity": "ERROR", "on_missing": "UNKNOWN"},
                }
                for key, measurement, parameters, threshold in [
                    ("text_completeness", "incomplete_text_ratio", {"min_char_count": 20}, 0.2),
                    ("text_purity", "interference_record_ratio", {"symbol_run_length": 8}, 0.4),
                ]
            ],
        }
        path = "/api/v1/quality-contract-versions"
        preview = client.post(f"{path}/preview", json=manual)
        assert preview.status_code == 200, preview.text
        assert preview.json()["contract"]["status"] == "DRAFT"
        assert len(client.get(path).json()) == 2
        incomplete = {**manual, "excluded_metrics": {}}
        assert client.post(f"{path}/multi", json=incomplete).status_code == 422
        manual_publish = client.post(f"{path}/multi", json=manual)
        assert manual_publish.status_code == 201, manual_publish.text
        manual_doc = manual_publish.json()["contract"]
        assert manual_doc["excluded_metrics"] == manual["excluded_metrics"]
        assert [row["rule"]["threshold"] for row in manual_doc["items"]] == [0.2, 0.4]
        assert manual_doc["items"][0]["calculation"]["parameters"]["min_char_count"] == 20
        assert manual_doc["items"][1]["calculation"]["parameters"]["symbol_run_length"] == 8
        with Session(engine) as session:
            contract = QualityContractService(session).get(UUID(manual_doc["contract_version_id"])).contract
            plan = LogicalPlannerService(session).compile(contract, session.get(DatasetVersionModel, version_id))
            assert {binding.contract_item_key for binding in plan.output_bindings} == {"text_completeness", "text_purity"}

    assert synced.status_code == 200
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
    assert "minimum" in rejected.json()["detail"]
    assert multi.status_code == 201
    assert [item["key"] for item in multi.json()["contract"]["items"]] == [
        "completeness",
        "duplicate",
    ]
    assert invalid_multi.status_code == 422
    assert len(listed_after_multi.json()) == 2

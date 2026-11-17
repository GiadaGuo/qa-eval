from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import qa_eval.calculators.sensitive_entity_detection.calculator as calculator_module
from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

ROOT = Path(__file__).resolve().parents[2]
NER_PYTHON = Path("/Users/daphne/miniconda3/envs/ner-eval/bin/python")
MODEL_DIR = ROOT / "experiments/bg06_ner/models/uie-mini"
VENDOR_SRC = ROOT / "experiments/bg06_ner/vendor/uie_pytorch/src"
MODEL_SHA256 = "sha256:84dcf7310492de333a202ba03fdb9081d15f53f0b2330c9c4842da219787c0a6"


class _NoopProvider:
    def __init__(self, **_kwargs):
        pass

    def infer(self, _text: str, _request_id: str):
        return []

    def close(self):
        pass


def _sample(version_id, index: int, text: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": f"synthetic-{index}",
            "contents": [
                {
                    "content_id": "body-plain",
                    "modality": "text",
                    "role": "article.body.plain",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": text, "format": "plain"},
                }
            ],
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": f"synthetic-{index}",
            },
            "lineage": {"input_fingerprint": "sha256:" + "a" * 64},
        },
        ensure_ascii=False,
    )


def test_sensitive_entity_evidence_to_record_issues_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(calculator_module, "IsolatedUIEProvider", _NoopProvider)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    texts = [
        "😀联系电话13800138000，备用电话13912345678。",
        "身份证11010519491231002X。",
    ]
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        "\n".join(_sample(version_id, i, text) for i, text in enumerate(texts)) + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="sensitive-entity-e2e", modality=["text"])
        )
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                content_fingerprint=f"sha256:{normalized.sha256}",
                artifact_refs={
                    "manifest": {"uri": "manifest.json"},
                    "normalized": {
                        "uri": normalized.uri,
                        "sha256": normalized.sha256,
                    },
                    "field_mapping": {
                        "mappings": [
                            {
                                "action": "MAP",
                                "target": {
                                    "kind": "CONTENT",
                                    "role": "article.body.plain",
                                    "modality": "text",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.flush()
        MetricRegistryService(session).sync()
        contract = (
            QualityContractService(session)
            .publish(
                QualityContractPublishRequest.model_validate(
                    {
                        "name": "0202 evidence issue e2e",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "0202",
                        "metric_version": "2.0.0",
                        "measurement": "sensitive_entity_hit_count",
                        "calculator_key": "bg_06_01_sensitive_entity_detection",
                        "calculator_version": "1.0.0",
                        "parameters": {
                            "ner_python": "/fake/python",
                            "model_dir": "/fake/model",
                            "vendor_src": "/fake/vendor",
                            "model_checksum": f"sha256:{'1' * 64}",
                            "max_records": 10,
                            "evidence_limit": 1,
                        },
                        "rule": {
                            "strategy": "OBSERVE_ONLY",
                            "severity": "WARNING",
                            "on_missing": "SKIPPED",
                        },
                    }
                )
            )
            .contract
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract_version_id)
        session.commit()
        completed = service.execute(run.run_id)
        result = service.list_results(run.run_id)[0]
        quality = QualityResultService(session, store)
        total, issues = quality.list_issues(run.run_id, limit=100)
        report = quality.get_report(run.run_id)
        rows = [
            json.loads(line)
            for line in store.read(result.evidence[0].artifact.uri)
            .decode()
            .splitlines()
        ]

        assert completed.status == "SUCCEEDED", completed.error
        assert result.status == "OK"
        assert result.evidence[0].summary.record_count == 3
        assert result.evidence[0].summary.attributes["detected_count"] == 3
        assert result.evidence[0].summary.attributes["emitted_count"] == 3
        assert (
            result.evidence[0].summary.attributes["completeness_status"] == "COMPLETE"
        )
        assert len(rows) == 3
        assert total == 2
        assert report is not None
        assert report.issue_summary.candidate_count == 2
        assert report.issue_summary.confirmed_count == 0
        assert report.metrics[0].evidence_ids == [result.evidence[0].evidence_id]
        trace = report.metrics[0].traceability_summary
        assert trace is not None
        assert trace.detected_fact_count == 3
        assert trace.persisted_evidence_record_count == 3
        assert trace.materialized_issue_count == 2
        assert trace.affected_record_count == 2
        assert trace.evidence_completeness == "COMPLETE"
        assert {issue.subject.sample_id for issue in issues} == {
            "synthetic-0",
            "synthetic-1",
        }
        phone_issue = next(
            issue
            for issue in issues
            if issue.code == "SENSITIVE_ENTITY_MOBILE_PHONE_CN"
        )
        assert phone_issue.scope == "CONTENT"
        assert phone_issue.determination == "CANDIDATE"
        assert len(phone_issue.evidence) == 2

        evidence_query = EvidenceQueryService(session, store)
        metadata = evidence_query.evidence_metadata(result.evidence[0].evidence_id)
        assert metadata is not None
        assert metadata["index"]["status"] == "NOT_BUILT"
        built = evidence_query.build_evidence_index(result.evidence[0].evidence_id)
        assert built is not None
        assert built["status"] == "READY"
        page = evidence_query.list_evidence_rows(
            result.evidence[0].evidence_id,
            limit=1,
            sample_id="synthetic-0",
        )
        assert page is not None
        first_page, cursor = page
        assert len(first_page) == 1
        assert cursor is not None
        second_page = evidence_query.list_evidence_rows(
            result.evidence[0].evidence_id,
            limit=1,
            cursor=cursor,
            sample_id="synthetic-0",
        )
        assert second_page is not None
        assert len(second_page[0]) == 1
        locations = evidence_query.issue_locations(phone_issue.issue_id)
        assert locations is not None
        assert len(locations["locations"]) == 2
        assert all(item["highlight"] is not None for item in locations["locations"])
        assert all(item["reason"] is None for item in locations["locations"])
        for location in locations["locations"]:
            span = location["highlight"]
            located = "".join(list(location["text"])[span["start"] : span["end"]])
            assert located in {"13800138000", "13912345678"}

        # Same-length source drift must degrade to field-only instead of
        # fabricating a highlight at stale offsets.
        normalized_path = store.root_dir / normalized.uri
        original_normalized = normalized_path.read_text(encoding="utf-8")
        normalized_path.write_text(
            original_normalized.replace("13800138000", "13700137000"),
            encoding="utf-8",
        )
        degraded = evidence_query.issue_locations(phone_issue.issue_id)
        assert degraded is not None
        changed = next(
            item
            for item in degraded["locations"]
            if item.get("sample_id") == "synthetic-0"
            and item.get("content_id") == "body-plain"
        )
        assert changed["highlight"] is None
        assert changed["precision"] == "FIELD_ONLY"
        assert changed["reason"] == "TEXT_VERSION_MISMATCH"
        normalized_path.write_text(original_normalized, encoding="utf-8")

        app = create_app()

        def override_session():
            yield session

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_artifact_store] = lambda: store
        with TestClient(app) as client:
            evidence_response = client.get(
                f"/api/v1/evidence/{result.evidence[0].evidence_id}"
            )
            rows_response = client.get(
                f"/api/v1/evidence/{result.evidence[0].evidence_id}/rows",
                params={"sample_id": "synthetic-0", "limit": 10},
            )
            issue_response = client.get(
                f"/api/v1/quality-issues/{phone_issue.issue_id}"
            )
            issue_evidence_response = client.get(
                f"/api/v1/quality-issues/{phone_issue.issue_id}/evidence"
            )
            location_response = client.get(
                f"/api/v1/quality-issues/{phone_issue.issue_id}/locations"
            )

        assert evidence_response.status_code == 200
        assert evidence_response.json()["index"]["status"] == "READY"
        assert "source_uri" not in evidence_response.json()["index"]
        assert len(rows_response.json()["rows"]) == 2
        assert issue_response.status_code == 200
        assert len(issue_evidence_response.json()["rows"]) == 2
        assert all(
            item["highlight"] is not None
            for item in location_response.json()["locations"]
        )


def test_sample_lookup_isolated_by_dataset_version(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id = uuid4()
    versions = [uuid4(), uuid4()]

    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="version-isolation", modality=["text"])
        )
        for index, version_id in enumerate(versions):
            relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
            staging = store.prepare_write(relative)
            staging.write_text(
                _sample(version_id, 0, f"version-{index}-text") + "\n",
                encoding="utf-8",
            )
            normalized = store.commit(relative)
            session.add(
                DatasetVersionModel(
                    id=version_id,
                    dataset_id=dataset_id,
                    version=f"v{index + 1}",
                    status="PUBLISHED",
                    content_fingerprint=f"sha256:{normalized.sha256}",
                    artifact_refs={
                        "manifest": {"uri": f"manifest-{index}.json"},
                        "normalized": {
                            "uri": normalized.uri,
                            "sha256": normalized.sha256,
                        },
                    },
                )
            )
        session.flush()
        query = EvidenceQueryService(session, store)
        first = query.get_sample_by_id(versions[0], "synthetic-0")
        second = query.get_sample_by_id(versions[1], "synthetic-0")

        assert first is not None and second is not None
        assert first.dataset_version_id == versions[0]
        assert second.dataset_version_id == versions[1]
        assert first.contents[0].payload.text == "version-0-text"
        assert second.contents[0].payload.text == "version-1-text"


@pytest.mark.skipif(
    not (
        NER_PYTHON.is_file()
        and (MODEL_DIR / "pytorch_model.bin").is_file()
        and VENDOR_SRC.is_dir()
    ),
    reason="local BG-06-01 UIE assets are unavailable",
)
def test_real_uie_dataset_contract_plan_run_result_evidence_report(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    texts = [
        "联系人身份证11010519491231002X，手机号13800138000。",
        "经查，涉案人员王某某于现场接受询问。",
        "公开征求意见稿提出：请社会公众反馈意见。",
        "内部材料：涉案人员陈某。批示：核实后再报。",
        "本办法适用于北京市、朝阳区以及海淀区。",
        "普通政策正文，不含任何个人敏感要素。",
        "联系电话13912345678。",
        "工作人员赵敏负责记录。",
        "送达地址为北京市朝阳区建国路88号。",
        "经核查，涉案人员周某住址待核实。",
    ]
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        "\n".join(_sample(version_id, i, text) for i, text in enumerate(texts)) + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="bg-06-01-synthetic", modality=["text"])
        )
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                content_fingerprint=f"sha256:{normalized.sha256}",
                artifact_refs={
                    "manifest": {"uri": "manifest.json"},
                    "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                    "field_mapping": {
                        "mappings": [
                            {
                                "action": "MAP",
                                "target": {
                                    "kind": "CONTENT",
                                    "role": "article.body.plain",
                                    "modality": "text",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.flush()
        MetricRegistryService(session).sync()
        contract = (
            QualityContractService(session)
            .publish(
                QualityContractPublishRequest.model_validate(
                    {
                        "name": "BG-06-01 synthetic observe-only",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "0202",
                        "metric_version": "2.0.0",
                        "measurement": "sensitive_entity_hit_count",
                        "calculator_key": "bg_06_01_sensitive_entity_detection",
                        "calculator_version": "1.0.0",
                        "parameters": {},
                        "rule": {
                            "strategy": "OBSERVE_ONLY",
                            "severity": "INFO",
                            "on_missing": "SKIPPED",
                        },
                    }
                )
            )
            .contract
        )
        service = EvaluationRunService(session, store)
        resolved = contract.items[0].calculation.parameters
        assert resolved["ner_python"] == str(NER_PYTHON)
        assert resolved["model_dir"] == str(MODEL_DIR)
        assert resolved["vendor_src"] == str(VENDOR_SRC)
        assert resolved["model_checksum"] == MODEL_SHA256
        run = service.create(contract.contract_version_id)
        session.commit()
        completed = service.execute(run.run_id)
        result = service.list_results(run.run_id)[0]
        stages = service.list_stage_runs(run.run_id)
        report = QualityResultService(session, store).get_report(run.run_id)
        values = {item.key: item.value for item in result.observations}
        evidence_rows = [
            json.loads(line)
            for line in store.read(result.evidence[0].artifact.uri)
            .decode()
            .splitlines()
        ]

        assert completed.status == "SUCCEEDED", completed.error
        assert stages[0].status == "SUCCEEDED"
        assert result.status == "OK"
        assert values["evaluated_record_count"] == 10
        assert values["sensitive_entity_hit_count"] >= 4
        assert values["sensitive_entity_type_counts"]["MOBILE_PHONE_CN"] >= 2
        assert any("NER" in row["channels"] for row in evidence_rows)
        assert any("RULE" in row["channels"] for row in evidence_rows)
        serialized = json.dumps(evidence_rows, ensure_ascii=False)
        assert "11010519491231002X" not in serialized
        assert "13800138000" not in serialized
        assert report is not None
        assert report.execution_status == "SUCCEEDED"
        assert report.gate_status == "PASS"

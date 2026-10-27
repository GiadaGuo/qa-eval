import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
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


def _sample(version_id, sample_id: str, text: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "body",
                    "modality": "text",
                    "role": "article.body.plain",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": text, "format": "plain"},
                    "attributes": {},
                }
            ],
            "annotations": [],
            "metadata": {},
            "source": {"source_artifact_id": str(uuid4()), "source_record_id": sample_id, "source_row": 1, "locator": None, "attributes": {}},
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def test_formal_run_executes_calculator_and_persists_metric_result(tmp_path: Path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    content = "\n".join([_sample(version_id, "a", "重复正文"), _sample(version_id, "b", "重复正文"), _sample(version_id, "c", "独立正文")]) + "\n"
    staging = store.prepare_write(relative)
    staging.write_text(content)
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        session.add(DatasetVersionModel(id=version_id, dataset_id=dataset_id, version="v1", status="PUBLISHED", content_fingerprint=f"sha256:{normalized.sha256}", artifact_refs={
            "manifest": {"uri": "manifest.json"},
            "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
            "field_mapping": {"mappings": [{"action": "MAP", "target": {"kind": "CONTENT", "role": "article.body.plain"}}]},
        }))
        session.flush()
        contract = QualityContractService(session).publish(QualityContractPublishRequest.model_validate({
            "name": "duplicate gate", "version_label": "v1", "dataset_version_id": version_id,
            "metric_key": "02080103", "metric_version": "1.0.0", "measurement": "exact_affected_record_ratio",
            "calculator_key": "text_duplicate_degree", "calculator_version": "2.0.0",
            "parameters": {"content_role": "article.body.plain"},
            "rule": {"operator": "LTE", "threshold": 0.5, "severity": "ERROR", "on_missing": "UNKNOWN"},
        }))
        service = EvaluationRunService(session, store)
        created = service.create(contract.contract.contract_version_id)
        session.commit()
        assert created.status == "PENDING"
        completed = service.execute(created.run_id)
        results = service.list_results(created.run_id)
        quality = QualityResultService(session, store)
        report = quality.get_report(created.run_id)
        rules = quality.list_rules(created.run_id)
        issue_total, issues = quality.list_issues(created.run_id)

    assert completed.status == "SUCCEEDED"
    assert len(results) == 1
    assert results[0].status == "OK"
    observations = {item.key: item.value for item in results[0].observations}
    assert observations["exact_affected_record_ratio"] == 2 / 3
    assert len(results[0].evidence) == 2
    assert all(store.exists(item.artifact.uri) for item in results[0].evidence)
    assert report is not None
    assert report.execution_status == "SUCCEEDED"
    assert report.gate_status == "FAIL"
    assert len(rules) == 1
    assert rules[0].status == "FAIL"
    assert issue_total == 3
    assert Counter(issue.determination for issue in issues) == {
        "CONFIRMED": 1,
        "CANDIDATE": 2,
    }

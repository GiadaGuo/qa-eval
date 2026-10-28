import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import PhysicalEvaluationPlanModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


class FakeSharedLLM:
    model = "fake-semantic"

    def __init__(self, *, omit: str | None = None, invalid: bool = False):
        self.stats = {"calls": 0, "failures": 0}
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.omit = omit
        self.invalid = invalid

    def complete_json(self, *, system: str, user: str):
        del system, user
        self.stats["calls"] += 1
        self.usage["prompt_tokens"] += 10
        self.usage["completion_tokens"] += 5
        self.usage["total_tokens"] += 15
        if self.invalid:
            return {"wrong": {}}
        outputs = {
            "knowledge_density": {"score": 0.8, "rationale": "facts", "flags": []},
            "information_missing": {"score": 0.2, "rationale": "complete", "flags": []},
            "text_coherence": {"score": 0.9, "rationale": "coherent", "flags": []},
        }
        outputs.pop(self.omit, None)
        return {"outputs": outputs}


def _sample(version_id, sample_id: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [{"content_id": "body", "modality": "text", "role": "article.body.plain", "presence": "PRESENT", "payload": {"type": "inline_text", "text": f"完整且连贯的政策文章 {sample_id}", "format": "plain"}, "attributes": {}}],
            "annotations": [],
            "metadata": {},
            "source": {"source_artifact_id": str(uuid4()), "source_record_id": sample_id, "source_row": 1, "locator": None, "attributes": {}},
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def _setup(tmp_path: Path, client: FakeSharedLLM):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    session = Session(engine)
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text("\n".join(_sample(version_id, str(index)) for index in range(3)) + "\n")
    normalized = store.commit(relative)
    session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
    session.add(DatasetVersionModel(id=version_id, dataset_id=dataset_id, version="v1", status="PUBLISHED", content_fingerprint=f"sha256:{normalized.sha256}", artifact_refs={"manifest": {"uri": "manifest.json"}, "normalized": {"uri": normalized.uri, "sha256": normalized.sha256}, "field_mapping": {"mappings": [{"action": "MAP", "target": {"kind": "CONTENT", "role": "article.body.plain"}}]}}))
    session.flush()
    MetricRegistryService(session).sync()
    sharing = {"allow_shared_execution": True, "provider": "fake", "model": client.model, "sampling_fingerprint": "first-3", "prompt_family": "text-quality-semantic-v1", "preprocessing_fingerprint": "plain-v1", "context_policy": "head-12000", "privacy_policy": "normalized-only", "input_selector": "article.body.plain", "independent_outputs": True}
    definitions = [
        ("density", "02080102", "mean_effective_knowledge_score", "knowledge_information_density", "GTE", 0.5),
        ("missing", "02080105", "key_element_missing_ratio", "information_missing_degree", "LTE", 0.5),
        ("coherence", "02080107", "coherent_text_ratio", "text_coherence", "GTE", 0.5),
    ]
    contract = QualityContractService(session).publish_multi(QualityContractMultiPublishRequest.model_validate({"name": "shared semantic", "version_label": "v1", "dataset_version_id": version_id, "items": [{"key": key, "metric_key": metric, "metric_version": "1.0.0", "measurement": measurement, "calculator_key": calculator, "calculator_version": "1.0.0", "parameters": {"content_role": "article.body.plain", "sample_limit": 3, "sharing": sharing}, "rule": {"operator": operator, "threshold": threshold, "severity": "ERROR", "on_missing": "UNKNOWN"}} for key, metric, measurement, calculator, operator, threshold in definitions]})).contract
    service = EvaluationRunService(session, store, llm_client=client)
    run = service.create(contract.contract_version_id)
    session.commit()
    return session, service, run


def test_three_metrics_share_one_provider_call_per_record(tmp_path: Path):
    client = FakeSharedLLM()
    session, service, run = _setup(tmp_path, client)
    completed = service.execute(run.run_id)
    results = service.list_results(run.run_id)
    stages = service.list_stage_runs(run.run_id)
    plan = session.scalar(select(PhysicalEvaluationPlanModel)).document

    assert completed.status == "SUCCEEDED"
    assert len(plan["stages"]) == 1
    assert plan["stages"][0]["stage_kind"] == "SHARED_INFERENCE"
    assert len(plan["stages"][0]["output_bindings"]) == 3
    assert client.stats["calls"] == 3
    assert len(stages) == 1
    assert stages[0].stats["provider_calls"] == 3
    assert stages[0].stats["token_usage"]["total_tokens"] == 45
    assert {item.metric.key for item in results} == {"02080102", "02080105", "02080107"}
    assert len({item.stage_run_id for item in results}) == 1
    session.close()


def test_missing_one_metric_output_does_not_become_zero(tmp_path: Path):
    client = FakeSharedLLM(omit="information_missing")
    session, service, run = _setup(tmp_path, client)
    completed = service.execute(run.run_id)
    results = {item.metric.key: item for item in service.list_results(run.run_id)}

    assert completed.status in {"SUCCEEDED", "PARTIAL_SUCCEEDED"}
    assert results["02080105"].status == "ERROR"
    assert results["02080105"].observations == []
    session.close()


def test_invalid_structured_response_fails_shared_stage(tmp_path: Path):
    client = FakeSharedLLM(invalid=True)
    session, service, run = _setup(tmp_path, client)
    completed = service.execute(run.run_id)
    results = service.list_results(run.run_id)

    assert completed.status == "FAILED"
    assert len(results) == 3
    assert all(item.status == "ERROR" for item in results)
    session.close()

"""The 0207 embedding binding uses the real resolver, planner and worker."""

from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from sqlalchemy.orm import Session
from test_m4_policy_logical_run import _seed

from apps.api.routes.quality_contracts import (
    ContractInputPreflightRequest,
    preflight_contract_inputs,
    prepare_multi_contract,
)
from qa_eval.application.evidence.raw_location import RawEvidenceLocation
from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.content_consistency import recall
from qa_eval.calculators.field_relation_consistency import embedding
from qa_eval.calculators.field_relation_consistency_v22 import resource_options
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.session import create_database_engine
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

IDENTITY = "bge:fixture:fixture-runtime"


def test_embedding_contract_worker_and_verifiable_evidence(tmp_path, monkeypatch):
    calls = []
    fail = False

    class Matrix(list):
        def tolist(self):
            return list(self)

    class TestModel:
        prompts: ClassVar = {}
        default_prompt_name = None
        max_seq_length = 40

        @staticmethod
        def tokenizer(text, *, truncation):
            assert not truncation
            return {"input_ids": list(text)}

        @staticmethod
        def encode(texts, **kwargs):
            assert len(texts) <= embedding.BATCH_SIZE
            assert len(set(texts)) == len(texts)
            assert all(len(text) <= TestModel.max_seq_length for text in texts)
            calls.append(texts)
            if fail:
                raise RuntimeError("offline fixture failure")
            return Matrix(
                [
                    [1.0, 0.0] if "补助" in text or "补贴" in text else [0.0, 1.0]
                    for text in texts
                ]
            )

    class TestEncoder(embedding.Encoder):
        def __init__(self, backend, path, cache, batch_size):
            assert backend == "bge"
            recall.Encoder.__init__(self, "concept", None, cache, batch_size)
            self.identity = IDENTITY
            self.model = TestModel()

    monkeypatch.delenv("QA_EVAL_0206_BGE_MODEL_DIR", raising=False)
    assert not resource_options()["models"][0]["available"]
    assert resource_options()["tfidf"][0]["id"] == "policy-reference-128-v1"
    database = create_database_engine(f"sqlite:///{tmp_path / 'data.sqlite'}")
    Base.metadata.create_all(database)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(database) as session:
        dataset_id = uuid4()
        session.add(DatasetModel(id=dataset_id, name="fixture", modality=["text"]))
        session.commit()
        body = "道路石头" * 32 + "申请补助"
        version_id = _seed(
            session,
            store,
            dataset_id,
            "3.0",
            [
                {"title": "企业补贴", "html": body},
                {"title": "企业补贴", "html": "天气预报"},
                {"title": "企业补贴", "html": None},
            ],
        )
        MetricRegistryService(session).sync()
        runner = EvaluationRunService(session, store)
        config = {
            "metric_key": "0207",
            "metric_version": "2.0.0",
            "calculator_key": "field_relation_consistency",
            "calculator_version": "2.2.0",
            "input_selectors": {
                "a": {"field_tokens": ["title"]},
                "b": {"field_tokens": ["html"]},
            },
            "parameters": {
                "fields": {"a": {"value_type": "string"}, "b": {"value_type": "text"}},
                "relations": [
                    {
                        "id": "topic",
                        "left": "a",
                        "right": "b",
                        "weight": 1,
                        "required": True,
                        "method": "embedding_match",
                        "parameters": {
                            "chunk_chars": 64,
                            "fail_below": 0.45,
                            "pass_at": 0.72,
                        },
                    }
                ],
            },
        }
        check = ContractInputPreflightRequest(dataset_version_id=version_id, **config)
        missing = preflight_contract_inputs(check, runner)
        assert missing["status"] == "INVALID_CONFIGURATION"
        assert "本地 BGE 模型" in missing["detail"]
        monkeypatch.setattr(
            embedding, "model_configuration", lambda: (Path("fixture"), IDENTITY)
        )
        monkeypatch.setattr(embedding, "Encoder", TestEncoder)
        monkeypatch.setenv(
            "QA_EVAL_0206_EMBEDDING_CACHE", str(tmp_path / "shared.sqlite")
        )
        assert preflight_contract_inputs(check, runner)["status"] == "READY"
        wrong_type = check.model_copy(deep=True)
        wrong_type.parameters["fields"]["b"]["value_type"] = "number"
        assert (
            preflight_contract_inputs(wrong_type, runner)["status"]
            == "INVALID_CONFIGURATION"
        )
        reversed_thresholds = check.model_copy(deep=True)
        reversed_thresholds.parameters["relations"][0]["parameters"]["pass_at"] = 0.4
        assert (
            preflight_contract_inputs(reversed_thresholds, runner)["status"]
            == "INVALID_CONFIGURATION"
        )

        request = QualityContractMultiPublishRequest.model_validate(
            {
                "dataset_version_id": version_id,
                "name": "0207 embedding",
                "version_label": "v1",
                "observation_items": [
                    {
                        **config,
                        "key": "topic",
                        "schema_version": "1.1",
                        "strategy": "EVALUATE",
                        "projections": [],
                        "severity": "ERROR",
                        "rule": {"operator": "GTE", "threshold": 1 / 3},
                        "statistic_ref": {
                            "contract_item_key": "topic",
                            "output_slot_id": "topic",
                            "calculator_version": "2.2.0",
                            "statistics_key": "confirmed_consistent_record_ratio",
                        },
                    }
                ],
            }
        )
        publisher = QualityContractService(session)
        contract = prepare_multi_contract(request, publisher, runner).contract
        restored = publisher.get(contract.contract_version_id)
        assert restored.contract == contract
        frozen = contract.observation_items[0].calculation.parameters["relations"][0][
            "parameters"
        ]
        assert frozen["model_identity"] == IDENTITY
        assert frozen["aggregation"] == "max"
        assert frozen["threshold_version"].startswith("trial-v1-")

        def execute():
            run = runner.create(contract.contract_version_id, task_type="SINGLE_METRIC")
            session.commit()
            executed = runner.execute(run.run_id)
            assert executed.status == "SUCCEEDED", executed.error
            (result,) = runner.list_calculator_results(run.run_id)
            (artifact,) = result.decision_artifact_refs
            return run, result, list(EvidenceReader(store).iter_jsonl(artifact))

        run, result, decisions = execute()
        assert result.statistics["confirmed_consistent_record_ratio"] == 1 / 3
        assert [row["status"] for row in decisions] == ["1", "0", "NOT_EVALUATED"]
        assert (
            QualityResultService(session, store)
            .finalize(run.run_id)
            .native_evaluations[0]
            .evaluation.status
            == "PASS"
        )
        assert len(calls) == 1
        assert "企业补贴" in calls[0]
        assert sum(text == "企业补贴" for text in calls[0]) == 1
        positive = decisions[0]
        edge = positive["attributes"]["relations"][0]
        assert edge["raw_score"] == 1
        assert edge["details"]["right_fragment"]["excerpt"] == "申请补助"
        location = next(
            item for item in positive["locations"] if item["content_id"] == "b"
        )
        origin = positive["attributes"]["source_origins"]["b"]
        evidence_service = EvidenceQueryService(session, store)
        record = evidence_service.get_sample_by_id(version_id, positive["sample_id"])
        evidence = RawEvidenceLocation.model_validate(
            {
                "schema_version": "3.0",
                "row_id": positive["row_id"],
                "sample_id": positive["sample_id"],
                "content_id": "b",
                "location": {
                    "field_tokens": origin["field_tokens"],
                    "artifact_sha256": origin["source_sha256"],
                    "text_sha256": origin["text_sha256"],
                    "start": location["start"],
                    "end": location["end"],
                },
            }
        )
        text = evidence.location.verified_text(record)
        assert text[location["start"] : location["end"]] == "申请补助"

        fail = True
        _, cached, _ = execute()
        assert cached.status_detail["embedding_cache_hits"] > 0
        assert cached.status_detail["embedding_cache_misses"] == 0
        assert len(calls) == 1

        monkeypatch.setenv(
            "QA_EVAL_0206_EMBEDDING_CACHE", str(tmp_path / "empty.sqlite")
        )
        _, failed, failures = execute()
        assert failed.status == "MISSING"
        assert failed.statistics["not_evaluated_record_count"] == 3
        assert (
            failures[0]["attributes"]["relations"][0]["reason"]
            == "EMBEDDING_MODEL_FAILED"
        )

        model_calls = len(calls)
        monkeypatch.setattr(
            embedding,
            "model_configuration",
            lambda: (Path("fixture"), "replacement-model"),
        )
        _, changed, decisions = execute()
        assert changed.statistics["not_evaluated_record_count"] == 3
        assert (
            decisions[0]["attributes"]["relations"][0]["reason"]
            == "EMBEDDING_MODEL_UNAVAILABLE"
        )
        assert len(calls) == model_calls


def test_embedding_aggregation_and_chunk_limit():
    def split(text, limit):
        class Tokenizer:
            @staticmethod
            def fits(value):
                return len(value) <= 4

        return embedding.split_field(
            text, {"chunk_chars": 8, "max_chunks": limit}, Tokenizer()
        )

    assert split("abcdefgh", 1) is None
    title = split("abcd", 1)
    body = split("abcdefgh", 2)
    vectors = {"abcd": (1.0, 0.0), "efgh": (0.0, 1.0)}
    parameters = {
        "model_identity": IDENTITY,
        "aggregation": "max",
        "chunk_chars": 8,
        "max_chunks": 2,
        "threshold_version": "trial-v1-test",
        "fail_below": 0.5,
        "pass_at": 0.8,
    }
    peak = embedding.match(parameters, title, body, vectors)
    coverage = embedding.match(
        parameters | {"aggregation": "bidirectional_mean"}, title, body, vectors
    )
    assert (peak.status, peak.raw_score) == ("PASS", 1)
    assert (coverage.status, coverage.raw_score) == ("UNKNOWN", 0.75)
    assert (
        embedding.match(parameters, title, None, vectors).reason
        == "EMBEDDING_CHUNK_LIMIT"
    )

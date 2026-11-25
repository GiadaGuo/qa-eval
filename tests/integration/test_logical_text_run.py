import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.mapping_revision_service import MappingRevisionService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.logical_inputs import FieldBinding, MappingRevision
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.session import create_database_engine
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


@pytest.mark.parametrize("empty", [False, True])
def test_logical_text_runs_on_frozen_confirmed_mapping(tmp_path, empty):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'isolated.sqlite'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    source = b'{"body":"<p>long sentence</p>","alternative":"x"}\n{"body":null}\n'
    if empty:
        source = b""
    uri = "raw.jsonl"
    store.prepare_write(uri).write_bytes(source)
    raw = store.commit(uri)
    dataset_id, version_id = uuid4(), uuid4()
    schema = "sha256:" + hashlib.sha256(b"body,alternative").hexdigest()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="logical", modality=["text"]))
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v3",
                status="PUBLISHED",
                artifact_refs={
                    "raw_record_manifest": {
                        "dataset_version_id": str(version_id),
                        "source_uri": uri,
                        "source_sha256": "sha256:" + raw.sha256,
                        "schema_fingerprint": schema,
                    }
                },
            )
        )
        session.commit()
        MetricRegistryService(session).sync()
        mappings = MappingRevisionService(session)
        mapping = MappingRevision(
            dataset_version_id=version_id,
            schema_fingerprint=schema,
            bindings=(
                FieldBinding(
                    semantic="document.body.plain",
                    field_tokens=("body",),
                    representation="text/plain",
                    source_representation="text/html",
                    transform="html_to_plain@1.0.0",
                ),
            ),
        )
        digest = mappings.confirm(mapping)
        contract = (
            QualityContractService(session)
            .publish(
                QualityContractPublishRequest.model_validate(
                    {
                        "name": "logical",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "02080104",
                        "metric_version": "1.0.0",
                        "measurement": "incomplete_text_ratio",
                        "calculator_key": "text_completeness",
                        "calculator_version": "1.1.0",
                        "parameters": {"min_char_count": 5},
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.6,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    }
                )
            )
            .contract
        )
        runner = EvaluationRunService(session, store)
        run = runner.create(contract.contract_version_id, mapping_digest=digest)
        mappings.confirm(
            mapping.model_copy(
                update={
                    "bindings": (
                        FieldBinding(
                            semantic="document.body.plain",
                            field_tokens=("alternative",),
                            representation="text/plain",
                        ),
                    )
                }
            )
        )
        session.commit()
        executed = runner.execute(run.run_id)
        assert executed.status == "SUCCEEDED", executed.error
        (result,) = runner.list_results(run.run_id)
        if empty:
            assert result.status == "NOT_APPLICABLE"
            assert not result.observations
            assert result.population.input_count == 0
            assert result.population.evaluated_count == 0
            report = QualityResultService(session, store).finalize(run.run_id)
            evaluation, = report.metrics[0].rule_evaluations
            assert evaluation.status == "SKIPPED"
            return
        values = {item.key: item.value for item in result.observations}
        assert values == {
            "incomplete_text_ratio": 0.5,
            "incomplete_text_record_count": 1,
            "evaluable_record_count": 2,
        }
        assert result.status == "OK"
        report = QualityResultService(session, store).finalize(run.run_id)
        assert report.metrics[0].rule_evaluations

        v2_id = uuid4()
        v2_uri = "normalized.jsonl"
        rows = []
        for number, text in enumerate(("long sentence", None), 1):
            item = {
                "content_id": "body",
                "role": "article.body.plain",
                "modality": "text",
                "presence": "PRESENT" if text is not None else "NULL",
            }
            if text is not None:
                item["payload"] = {
                    "type": "inline_text",
                    "text": text,
                    "format": "plain",
                }
            rows.append(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "dataset_version_id": str(v2_id),
                        "sample_id": str(number),
                        "contents": [item],
                        "annotations": [],
                        "metadata": {},
                        "source": {"source_artifact_id": str(uuid4())},
                        "lineage": {"input_fingerprint": schema, "transforms": []},
                    }
                )
            )
        store.prepare_write(v2_uri).write_text("\n".join(rows) + "\n")
        normalized = store.commit(v2_uri)
        session.add(
            DatasetVersionModel(
                id=v2_id,
                dataset_id=dataset_id,
                version="v2",
                status="PUBLISHED",
                artifact_refs={
                    "normalized": {"uri": v2_uri, "sha256": normalized.sha256},
                    "manifest": {"uri": "v2-manifest.json"},
                    "field_mapping": {
                        "mappings": [
                            {
                                "action": "MAP",
                                "target": {
                                    "kind": "CONTENT",
                                    "modality": "text",
                                    "role": "article.body.plain",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.commit()
        old_contract = (
            QualityContractService(session)
            .publish(
                QualityContractPublishRequest.model_validate(
                    {
                        "name": "legacy",
                        "version_label": "v1",
                        "dataset_version_id": str(v2_id),
                        "metric_key": "02080104",
                        "metric_version": "1.0.0",
                        "measurement": "incomplete_text_ratio",
                        "calculator_key": "text_completeness",
                        "calculator_version": "1.0.0",
                        "parameters": {"min_char_count": 5},
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.6,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    }
                )
            )
            .contract
        )
        old_run = runner.create(old_contract.contract_version_id)
        session.commit()
        old_execution = runner.execute(old_run.run_id)
        assert old_execution.status == "SUCCEEDED", old_execution.error
        (old_result,) = runner.list_results(old_run.run_id)
        assert old_result.observations == result.observations
        assert old_result.population == result.population
        assert old_result.status == result.status

        corrupted = runner.create(contract.contract_version_id, mapping_digest=digest)
        session.commit()
        store.prepare_write(uri).write_bytes(source.replace(b"long sentence", b"short"))
        store.commit(uri)
        assert runner.execute(corrupted.run_id).status == "FAILED"

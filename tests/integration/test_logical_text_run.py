import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from qa_eval.application.normalization.input_resolver import FrozenInputPlan
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
from qa_eval.infrastructure.db.models.evaluation_run import PhysicalEvaluationPlanModel
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel
from qa_eval.infrastructure.db.session import create_database_engine
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


@pytest.mark.parametrize("empty", [False, True])
def test_logical_text_runs_on_frozen_confirmed_mapping(tmp_path, empty, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'isolated.sqlite'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    source = b'{"body":"<p>long sentence !!!!!!!!!!</p>","alternative":"x"}\n{"body":"<p>long sentence !!!!!!!!!!</p>"}\n{"body":null}\n'
    if empty:
        source = b""
    uri = "raw.jsonl"
    store.prepare_write(uri).write_bytes(source)
    raw = store.commit(uri)
    materialize = store.materialize

    def reject_raw_copy(relative_path, destination):
        assert relative_path != uri, "local v3 Worker should read the original in place"
        return materialize(relative_path, destination)

    monkeypatch.setattr(store, "materialize", reject_raw_copy)

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
        mappings.set_default(version_id, digest)
        run = runner.create(contract.contract_version_id)
        replacement = mappings.confirm(
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
        mappings.set_default(version_id, replacement)
        assert mappings.get(version_id, digest) == mapping
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
            "incomplete_text_ratio": 1 / 3,
            "incomplete_text_record_count": 1,
            "evaluable_record_count": 3,
        }
        assert result.status == "OK"
        report = QualityResultService(session, store).finalize(run.run_id)
        assert report.metrics[0].rule_evaluations

        v2_id = uuid4()
        v2_uri = "normalized.jsonl"
        rows = []
        for number, text in enumerate(("long sentence !!!!!!!!!!", "long sentence !!!!!!!!!!", None), 1):
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

        for metric_key, metric_version, calculator, measurement in (
            ("02080106", "1.0.0", "text_purity", "interference_record_ratio"),
            ("0208", "2.0.0", "text_purity", "interference_record_ratio"),
            ("02080103", "1.0.0", "exact_text_duplicate", "exact_affected_record_ratio"),
        ):
            outputs = []
            for target, calculator_version in ((v2_id, "1.0.0"), (version_id, "1.1.0")):
                request = QualityContractPublishRequest.model_validate({
                    "name": "text parity", "version_label": "v1",
                    "dataset_version_id": str(target), "metric_key": metric_key,
                    "metric_version": metric_version, "measurement": measurement,
                    "calculator_key": calculator, "calculator_version": calculator_version,
                    "rule": {"operator": "LTE", "threshold": 0.6,
                             "severity": "ERROR", "on_missing": "UNKNOWN"},
                })
                purity = QualityContractService(session).publish(request).contract
                run_purity = runner.create(
                    purity.contract_version_id, mapping_digest=digest if target == version_id else None,
                )
                session.commit()
                execution = runner.execute(run_purity.run_id)
                assert execution.status == "SUCCEEDED", execution.error
                result_purity, = runner.list_results(run_purity.run_id)
                QualityResultService(session, store).finalize(run_purity.run_id)
                if target == version_id and calculator == "exact_text_duplicate":
                    artifact, = result_purity.evidence
                    groups = json.loads(store.read(artifact.artifact.uri))
                    group, = groups["groups"]
                    assert group["record_count"] == 2
                    assert len(set(group["sample_ids"])) == 2
                    assert len(groups["excluded"]) == 1
                if target == version_id and calculator == "text_purity":
                    artifact, = result_purity.evidence
                    evidence = store.read(artifact.artifact.uri).splitlines()
                    assert len(evidence) == 2
                    row = json.loads(evidence[0])
                    assert row["coordinate_space"] == "RESOLVED_TEXT"
                    assert row["source_origin"]["field_tokens"] == ["body"]
                    assert row["source_origin"]["source_sha256"] == "sha256:" + raw.sha256
                    assert row["source_origin"]["transform"] == "html_to_plain@1.0.0"
                    assert "location" not in row
                outputs.append(result_purity)
            assert outputs[0].observations == outputs[1].observations
            assert outputs[0].population == outputs[1].population

        aggregate = QualityContractService(session).publish(QualityContractPublishRequest.model_validate({
            "name": "aggregate completeness", "version_label": "v1", "dataset_version_id": str(version_id),
            "metric_key": "0208", "metric_version": "2.0.0", "measurement": "incomplete_text_ratio",
            "calculator_key": "text_completeness", "calculator_version": "1.1.0",
            "parameters": {"min_char_count": 5},
            "rule": {"operator": "LTE", "threshold": 0.6, "severity": "ERROR", "on_missing": "UNKNOWN"},
        })).contract
        aggregate_run = runner.create(aggregate.contract_version_id, mapping_digest=digest)
        session.commit()
        assert runner.execute(aggregate_run.run_id).status == "SUCCEEDED"
        aggregate_result, = runner.list_results(aggregate_run.run_id)
        assert aggregate_result.observations == result.observations

        descriptor = session.scalar(select(CalculatorImplementationModel).filter_by(
            calculator_key="text_completeness", version="1.1.0",
        ))
        document = descriptor.document
        fixture = json.loads(json.dumps(document))
        fixture["input_contract"]["logical"]["slots"][0]["selectable"] = True
        descriptor.document = fixture
        session.flush()
        request = QualityContractPublishRequest.model_validate({
            "name": "selectable fixture", "version_label": "v1",
            "dataset_version_id": str(version_id),
            "metric_key": "02080104", "metric_version": "1.0.0",
            "measurement": "incomplete_text_ratio", "calculator_key": "text_completeness",
            "calculator_version": "1.1.0", "mapping_digest": digest,
            "input_selectors": {"body": {"field_tokens": ["alternative"]}},
            "rule": {"operator": "LTE", "threshold": 0.6,
                     "severity": "ERROR", "on_missing": "UNKNOWN"},
        })
        configured = QualityContractService(session).publish(request).contract
        session.commit()
        restored = QualityContractService(session).get(configured.contract_version_id).contract
        assert restored.items[0].calculation.implementation_constraints.attributes[
            "input_selectors"
        ] == {"body": {"field_tokens": ["alternative"]}}
        configured_run = runner.create(restored.contract_version_id)
        physical = session.get(PhysicalEvaluationPlanModel, configured_run.physical_plan_id)
        frozen = FrozenInputPlan.model_validate(physical.document["stages"][0]["calculator"]["frozen_input"])
        assert frozen.selectors["body"].field_tokens == ("alternative",)
        assert frozen.revision_fingerprint == digest
        session.commit()
        assert runner.execute(configured_run.run_id).status == "SUCCEEDED"
        fixture["input_contract"]["logical"]["artifact_kinds"] = ["DATASET_MANIFEST"]
        fixture["input_artifact_requirements"].append("DATASET_MANIFEST")
        descriptor.document = fixture
        flag_modified(descriptor, "document")
        session.flush()
        manifest_uri = "independent-manifest.json"
        store.prepare_write(manifest_uri).write_text("{}")
        manifest_artifact = store.commit(manifest_uri)
        version = session.get(DatasetVersionModel, version_id)
        version.artifact_refs = {
            **version.artifact_refs,
            "manifest": {"uri": manifest_uri, "sha256": manifest_artifact.sha256},
        }
        input_run = runner.create(restored.contract_version_id)
        tampered_run = runner.create(restored.contract_version_id)
        session.commit()
        assert runner.execute(input_run.run_id).status == "SUCCEEDED"
        store.prepare_write(manifest_uri).write_text('{"changed":true}')
        store.commit(manifest_uri)
        assert runner.execute(tampered_run.run_id).status == "FAILED"
        descriptor.document = document
        session.flush()

        corrupted = runner.create(contract.contract_version_id, mapping_digest=digest)
        session.commit()
        store.prepare_write(uri).write_bytes(source.replace(b"long sentence !!!!!!!!!!", b"short"))
        store.commit(uri)
        assert runner.execute(corrupted.run_id).status == "FAILED"

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    ContractRuleRequest,
    QualityContractItemPublishRequest,
    QualityContractMultiPublishRequest,
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.policy_structure_compliance import (
    PolicyStructureComplianceCalculator,
)
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _sample(version_id, sample_id: str, html: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "title",
                    "modality": "text",
                    "role": "article.title",
                    "presence": "PRESENT",
                    "payload": {
                        "type": "inline_text",
                        "text": "测试通知",
                        "format": "plain",
                    },
                    "attributes": {},
                },
                {
                    "content_id": "body-html",
                    "modality": "text",
                    "role": "article.body.html",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": html, "format": "html"},
                    "attributes": {
                        "source_paths": ["$.content"],
                        "transform": "identity",
                    },
                },
            ],
            "annotations": [],
            "metadata": {"extra": {"policyType": "通知"}},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def test_policy_structure_registered_loadable_and_old_0204_unchanged():
    with Session(
        create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    ) as session:
        Base.metadata.create_all(session.bind)
        service = MetricRegistryService(session)
        service.sync()
        descriptor = service.get_calculator("policy_structure_compliance", "1.0.0")
        assert descriptor is not None
        assert isinstance(
            TrustedCalculatorLoader().load(descriptor),
            PolicyStructureComplianceCalculator,
        )
        metric = service.get_metric("0204", "2.0.0")
        assert metric is not None and metric.parent_metric_key is None
        assert service.get_metric("02040305", "1.0.0") is None
        old = service.get_metric("0204", "1.0.0")
        assert old is not None and {item.key for item in old.measurements} == {
            "complete_record_ratio",
            "complete_record_count",
            "missing_record_count",
            "evaluable_record_count",
        }


def test_dataset_version_to_multi_observation_metric_result_and_evidence(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        _sample(version_id, "ok", "<p>第一条 总则</p><p>第二条 职责</p>")
        + "\n"
        + _sample(version_id, "gap", "<p>第一条 总则</p><p>第四条 职责</p>")
        + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)
    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="policy-structure-test", modality=["text"])
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
                                    "role": "article.body.html",
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
                        "name": "BG-02-03/04/05",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "0204",
                        "metric_version": "2.0.0",
                        "measurement": "parsing_success_rate",
                        "calculator_key": "policy_structure_compliance",
                        "calculator_version": "1.0.0",
                        "parameters": {},
                        "rule": {
                            "operator": "GTE",
                            "threshold": 0.9,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    }
                )
            )
            .contract
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract_version_id)
        session.commit()
        executed = service.execute(run.run_id)
        result = service.list_results(run.run_id)[0]
        values = {item.key: item.value for item in result.observations}
        rows = [
            json.loads(line)
            for line in store.read(result.evidence[0].artifact.uri)
            .decode()
            .splitlines()
        ]
        assert executed.status == "SUCCEEDED", executed.error
        assert result.status == "PARTIAL"
        assert values["parsing_success_rate"] == 1
        assert values["numbering_compliance_rate"] == 0.5
        assert values["numbering_defect_count"] == 1
        assert values["segmentation_correct_status_counts"]["UNAVAILABLE"] == 2
        assert {row["sample_id"] for row in rows} == {"ok", "gap"}
        assert all(row["document_type"]["value"] == "通知" for row in rows)
        assert result.evidence[0].redaction.contains_raw_content is False


def test_same_0204_contract_executes_and_reports_two_calculators(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        _sample(version_id, "ok", "<p>第一条 总则</p><p>第二条 职责</p>") + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="policy-both", modality=["text"]))
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
                                    "role": "article.body.html",
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
        with pytest.raises(
            ValueError,
            match="does not produce the requested measurement",
        ):
            QualityContractService(session).publish(
                QualityContractPublishRequest(
                    name="invalid calculator measurement",
                    version_label="v1-invalid",
                    dataset_version_id=version_id,
                    metric_key="0204",
                    metric_version="2.0.0",
                    measurement="parsing_success_rate",
                    calculator_key="structural_completeness",
                    calculator_version="1.0.0",
                    parameters={"content_role": "article.body.html"},
                    rule=ContractRuleRequest(
                        operator="GTE",
                        threshold=1,
                        severity="ERROR",
                        on_missing="UNKNOWN",
                    ),
                )
            )
        contract = QualityContractService(session).publish_multi(
            QualityContractMultiPublishRequest(
                name="0204 two methods",
                version_label="v1",
                dataset_version_id=version_id,
                items=[
                    QualityContractItemPublishRequest(
                        key="0204-fields",
                        metric_key="0204",
                        metric_version="2.0.0",
                        measurement="complete_record_ratio",
                        calculator_key="structural_completeness",
                        calculator_version="1.0.0",
                        parameters={"content_role": "article.body.html"},
                        rule=ContractRuleRequest(
                            operator="GTE",
                            threshold=1,
                            severity="ERROR",
                            on_missing="UNKNOWN",
                        ),
                    ),
                    QualityContractItemPublishRequest(
                        key="0204-policy",
                        metric_key="0204",
                        metric_version="2.0.0",
                        measurement="parsing_success_rate",
                        calculator_key="policy_structure_compliance",
                        calculator_version="1.0.0",
                        parameters={},
                        rule=ContractRuleRequest(
                            operator="GTE",
                            threshold=1,
                            severity="ERROR",
                            on_missing="UNKNOWN",
                        ),
                    ),
                ],
            )
        ).contract
        run_service = EvaluationRunService(session, store)
        run = run_service.create(contract.contract_version_id)
        session.commit()
        executed = run_service.execute(run.run_id)
        results = run_service.list_results(run.run_id)
        report = QualityResultService(session, store).finalize(run.run_id)

        assert executed.status == "SUCCEEDED", executed.error
        assert len(results) == 2
        assert len({item.metric_result_id for item in results}) == 2
        assert all(item.metric.key == "0204" for item in results)
        assert {
            evaluation.contract_item_key
            for metric in report.metrics
            for evaluation in metric.rule_evaluations
        } == {
            "0204-fields",
            "0204-policy",
        }
        assert len(report.metrics) == 2
        assert {item.calculator.key for item in report.metrics} == {
            "structural_completeness",
            "policy_structure_compliance",
        }

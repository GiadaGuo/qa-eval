import json
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_policy_structure_compliance_run import _sample

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.calculator_results import CalculatorDecisionV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.quality_result import QualityIssueModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def seed_run(session, store, dataset_id, version_id, normalized, parameters):
    session.add(
        DatasetModel(id=dataset_id, name="policy-consistency", modality=["text"])
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
    registry = MetricRegistryService(session)
    registry.sync()
    assert (
        registry.get_calculator("content_consistency", "1.0.0").execution.mode
        == "SHARED_INFERENCE"
    )
    key = "0206-body"
    contract = (
        QualityContractService(session)
        .publish_multi(
            QualityContractMultiPublishRequest.model_validate(
                {
                    "dataset_version_id": str(version_id),
                    "name": "0206 isolated",
                    "version_label": "v1",
                    "items": [],
                    "observation_items": [
                        {
                            "schema_version": "1.1",
                            "key": key,
                            "strategy": "EVALUATE",
                            "metric_key": "0206",
                            "metric_version": "2.0.0",
                            "calculator_key": "content_consistency",
                            "calculator_version": "2.0.0",
                            "parameters": parameters,
                            "projections": [],
                            "statistic_ref": {
                                "contract_item_key": key,
                                "output_slot_id": key,
                                "calculator_version": "2.0.0",
                                "statistics_key": "consistent_record_ratio",
                            },
                            "rule": {"operator": "GTE", "threshold": 0.9},
                            "severity": "WARNING",
                        }
                    ],
                }
            )
        )
        .contract
    )
    service = EvaluationRunService(session, store)
    run = service.create(contract.contract_version_id)
    session.commit()
    return service, run


def test_native_consistency_dataset_to_report(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = "sample_records.jsonl"
    body = "<p>第一条 企业补贴金额为1万元。</p><p>第二条 企业补贴金额为2万元。</p>"
    store.prepare_write(relative).write_text(
        "\n".join(
            (
                _sample(version_id, "conflict", body),
                _sample(version_id, "compatible", body.replace("2万元", "10000元")),
                _sample(version_id, "missing", "<p>null</p>"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    with Session(engine) as session:
        service, run = seed_run(
            session, store, dataset_id, version_id, store.commit(relative), {}
        )
        completed = service.execute(run.run_id)
        assert completed.status == "SUCCEEDED", completed.error
        [result] = service.list_calculator_results(run.run_id)
        assert result.dataset_version_id == version_id
        assert result.run_id == run.run_id
        assert result.statistics["consistent_record_ratio"] == 0.5
        assert result.statistics["coverage"] == 2 / 3
        assert result.statistics["all_input_consistent_ratio"] == 1 / 3
        [artifact] = result.decision_artifact_refs
        rows = [
            CalculatorDecisionV1.model_validate(row)
            for row in EvidenceReader(store).iter_jsonl(artifact)
        ]
        assert [row.status for row in rows] == ["0", "1", "NOT_EVALUATED"]
        left, right = rows[0].locations
        assert body[left.start : left.end] == "第一条 企业补贴金额为1万元。"
        assert body[right.start : right.end] == "第二条 企业补贴金额为2万元。"
        report = QualityResultService(session, store).finalize(run.run_id)
        [section] = report.native_evaluations
        assert section.evaluation.calculator_result_id == result.calculator_result_id
        assert section.evaluation.metric_value == 0.5
        assert section.evidence_count == section.issue_count == 1
        [issue] = session.query(QualityIssueModel).filter_by(run_id=run.run_id).all()
        assert issue.document["evidence"][0]["evidence_id"] == str(artifact.evidence_id)
        assert issue.document["evidence"][0]["locator"]["row_id"] == "conflict"
        assert json.loads(rows[0].model_dump_json())["attributes"]["score"] == 0.6

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_policy_structure_compliance_run import _sample

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.application.services.evaluation_recommendation_service import (
    EvaluationRecommendationService,
)
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.field_relation_consistency.configuration import SCHEMA_PATH
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.contracts.calculator_results import CalculatorDecisionV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.quality_result import QualityIssueModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def use_policy_config():
    return json.loads(
        Path("configs/field_relation_consistency/policy.json").read_bytes()
    )


def policy_row(version, sample, title, body, number, category="政策"):
    row = json.loads(_sample(version, sample, body))
    row["contents"][0]["payload"]["text"] = title
    row["contents"][1].update(role="article.body.plain", content_id="body-plain")
    row["contents"][1]["payload"]["format"] = "plain"
    row["metadata"] = {
        "extra": {"type": category},
        "document_number": {"filenumber": number},
    }
    return json.dumps(row, ensure_ascii=False)


def install_dataset(session, store, version, rows):
    dataset = uuid4()
    uri = "sample_records.jsonl"
    store.prepare_write(uri).write_text("\n".join(rows) + "\n", encoding="utf-8")
    artifact = store.commit(uri)
    session.add(DatasetModel(id=dataset, name="local policy test", modality=["text"]))
    session.add(
        DatasetVersionModel(
            id=version,
            dataset_id=dataset,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{artifact.sha256}",
            artifact_refs={
                "manifest": {"uri": "manifest.json"},
                "field_mapping": json.loads(
                    Path("configs/mapping_templates/policy.policy.json").read_bytes()
                )["field_mapping"],
                "normalized": {"uri": uri, "sha256": artifact.sha256},
            },
        )
    )
    session.flush()


def test_formal_field_relation_default_end_to_end(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    version = uuid4()
    title = "企业人才补贴申请办法"
    body = "\n第一条 企业人才补贴申请办法按本办法施行。" * 12
    same = "政发〔2024〕1号"
    rows = [
        policy_row(version, "positive", title, "发文字号：" + same + body, same),
        policy_row(
            version, "negative", title, "发文字号：政发〔2024〕2号" + body, same
        ),
        policy_row(version, "unknown", title, "null", None, "13"),
    ]
    with Session(engine) as session:
        install_dataset(session, store, version, rows)
        registry = MetricRegistryService(session)
        registry.sync()
        assert not next(
            binding
            for binding in registry.list_bindings("0207")
            if binding.metric_version == "1.0.0"
        ).native_only
        session.add(
            DatasetProfileModel(
                dataset_version_id=version,
                document={},
                checksum_sha256="a" * 64,
                profiler_key="test",
                profiler_version="1",
                config_fingerprint="test",
            )
        )
        session.flush()
        recommendation = EvaluationRecommendationService(session).recommend(
            version, revision=1
        )
        [default] = [
            item for item in recommendation.proposal.items if item.metric.key == "0207"
        ]
        assert (default.metric.version, default.metric.measurement) == (
            "2.0.0",
            "confirmed_consistent_record_ratio",
        )
        assert default.selected_calculator.key == "field_relation_consistency"
        assert default.parameters == use_policy_config()
        assert all(item.metric.key != "0206" for item in recommendation.proposal.items)
        legacy = registry.get_calculator("dataset_type_consistency", "1.0.0")
        assert (
            TrustedCalculatorLoader().load(legacy).calculator_key
            == legacy.calculator_key
        )
        descriptor = registry.get_calculator("field_relation_consistency", "2.0.0")
        assert descriptor.parameter_schema == json.loads(SCHEMA_PATH.read_bytes())
        assert TrustedCalculatorLoader().load(descriptor).version == "2.0.0"
        service = QualityContractService(session)
        old_contract = service.publish_multi(
            QualityContractMultiPublishRequest.model_validate(
                {
                    "dataset_version_id": str(version),
                    "name": "historical 0207 shape",
                    "version_label": "v1",
                    "items": [
                        {
                            "metric_key": "0207",
                            "metric_version": "1.0.0",
                            "calculator_key": "dataset_type_consistency",
                            "calculator_version": "1.0.0",
                            "measurement": "type_consistent_record_ratio",
                            "parameters": {"declared_dataset_type": "INDUSTRY_GENERAL"},
                            "rule": {
                                "operator": "GTE",
                                "threshold": 0.5,
                                "severity": "ERROR",
                                "on_missing": "UNKNOWN",
                            },
                        }
                    ],
                }
            )
        ).contract
        assert old_contract.items[0].metric.version == "1.0.0"
        assert not old_contract.observation_items
        default.rule.threshold = 0.5
        official = recommendation.proposal.model_copy(update={"items": [default]})
        [official_item] = EvaluationProposalService(session).to_contract_items(official)
        companion = {
            "key": "text-completeness",
            "metric_key": "02080104",
            "metric_version": "1.0.0",
            "calculator_key": "text_completeness",
            "calculator_version": "1.0.0",
            "measurement": "incomplete_text_ratio",
            "rule": {
                "operator": "LTE",
                "threshold": 1,
                "severity": "ERROR",
                "on_missing": "UNKNOWN",
            },
        }
        request = {
            "dataset_version_id": str(version),
            "name": "0207 formal evaluation",
            "version_label": "v2",
            "items": [official_item.model_dump(), companion],
        }
        duplicate = copy.deepcopy(request)
        relations = duplicate["items"][0]["parameters"]["relations"]
        relations[1]["id"] = relations[0]["id"]
        with pytest.raises(ValueError, match="duplicate relation id"):
            service.publish_multi(
                QualityContractMultiPublishRequest.model_validate(duplicate)
            )
        overlap = copy.deepcopy(request)
        overlap["items"].append(
            {
                "metric_key": "0206",
                "metric_version": "1.0.0",
                "calculator_key": "content_consistency",
                "calculator_version": "1.0.0",
                "measurement": "consistent_record_ratio",
                "rule": {
                    "operator": "GTE",
                    "threshold": 0.5,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        )
        with pytest.raises(ValueError, match="overlaps with 0207"):
            service.publish_multi(
                QualityContractMultiPublishRequest.model_validate(overlap)
            )
        contract = service.publish_multi(
            QualityContractMultiPublishRequest.model_validate(request)
        ).contract
        assert len(contract.items) == len(contract.observation_items) == 1
        runs = EvaluationRunService(session, store)
        run = runs.create(contract.contract_version_id)
        session.commit()
        completed = runs.execute(run.run_id)
        assert completed.status == "SUCCEEDED", completed.error
        [result] = runs.list_calculator_results(run.run_id)
        assert result.metric.version == "2.0.0"
        assert result.statistics["consistent_among_evaluable"] == 0.5
        assert result.statistics["evaluable_record_ratio"] == 2 / 3
        assert result.statistics["confirmed_consistent_record_ratio"] == 1 / 3
        assert result.status_detail["business_calibration_status"] == "PENDING"
        [artifact] = result.decision_artifact_refs
        decisions = [
            CalculatorDecisionV1.model_validate(row)
            for row in EvidenceReader(store).iter_jsonl(artifact)
        ]
        assert [row.status for row in decisions] == ["1", "0", "NOT_EVALUATED"]
        assert decisions[1].attributes["relations"][-1]["veto"]
        assert (
            decisions[2].attributes["relations"][1]["reason"]
            == "FIELD_PRESENT_PLACEHOLDER"
        )
        report = QualityResultService(session, store).finalize(run.run_id)
        [section] = report.native_evaluations
        assert section.evaluation.metric_value == 1 / 3
        assert section.evaluation.status == "FAIL"
        assert report.gate_status == "FAIL"
        assert section.evidence_count == section.issue_count == 1
        assert any(metric.metric.key == "02080104" for metric in report.metrics)
        operational = QualityResultService(session, store).rebuild_operational_report(
            run.run_id
        )
        assert operational.scoring_profile_version == "2.0.0"
        axis = next(item for item in operational.radar if item.dimension == "0207")
        [contribution] = axis.contributing_metrics
        assert contribution.measurement_key == "confirmed_consistent_record_ratio"
        assert contribution.raw_value == 1 / 3
        assert contribution.coverage_ratio == 2 / 3
        assert axis.deterministic_score == pytest.approx(33.3333)
        [issue] = session.query(QualityIssueModel).filter_by(run_id=run.run_id).all()
        locator = issue.document["evidence"][0]["locator"]
        assert locator["row_id"] == "negative"
        assert (
            locator["left"]["source"]["path"] == "/metadata/document_number/filenumber"
        )
        assert locator["right"]["source"]["content_id"] == "body-plain"
        assert locator["score"] == 0
        assert (
            locator["left"]["value_summary"]["sha256"]
            != locator["right"]["value_summary"]["sha256"]
        )
        assert "政发" not in json.dumps(issue.document, ensure_ascii=False)

from __future__ import annotations

import json
from pathlib import Path

from qa_eval.application.services.dataset_version_service import DatasetVersionService
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_profile import (
    SQLAlchemyDatasetProfileRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/e2e-text-quality-baseline.jsonl"


def _mapping() -> FieldMappingSpecV2:
    return FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
            "mappings": [
                {
                    "source_path": "$.sample_id",
                    "action": "MAP",
                    "target": {"kind": "SAMPLE_ID"},
                    "value_type": "string",
                    "presence_policy": "REJECT_MISSING",
                    "required_for_normalization": True,
                },
                {
                    "source_path": "$.text",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.plain",
                        "modality": "text",
                    },
                    "value_type": "string|null",
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                },
            ],
        }
    )


def test_upload_to_report_baseline(db_session, tmp_path: Path) -> None:
    """闭环 0 的最小真实链路，固定覆盖空文本、exact 与 near candidate。"""

    dataset_repository = SQLAlchemyDatasetRepository(db_session)
    version_repository = SQLAlchemyDatasetVersionRepository(db_session)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    versions = DatasetVersionService(
        dataset_repository=dataset_repository,
        version_repository=version_repository,
        profile_repository=SQLAlchemyDatasetProfileRepository(db_session),
        artifact_store=store,
    )

    dataset = dataset_repository.add(
        Dataset(name="closure-0-e2e-text", modality=["text"])
    )
    version = versions.create_version(dataset.id, "baseline-v1")
    versions.upload_raw_file(version.id, FIXTURE.name, FIXTURE.read_bytes())
    versions.save_field_mapping(version.id, _mapping())
    published = versions.publish_version(version.id)

    assert published["counts"] == {"input": 7, "normalized": 7, "rejected": 0}

    MetricRegistryService(db_session).sync()
    contract = QualityContractService(db_session).publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "闭环 0 重复内容基线",
                "version_label": "v1.0.0",
                "dataset_version_id": version.id,
                "metric_key": "02080103",
                "metric_version": "1.0.0",
                "measurement": "exact_affected_record_ratio",
                "calculator_key": "text_duplicate_degree",
                "calculator_version": "2.0.0",
                "parameters": {
                    "content_role": "article.body.plain",
                    "near_duplicate_threshold": 0.8,
                },
                "rule": {
                    "operator": "LTE",
                    "threshold": 0.1,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        )
    )
    runs = EvaluationRunService(db_session, store)
    run = runs.create(contract.contract.contract_version_id)
    db_session.commit()
    completed = runs.execute(run.run_id)
    result = runs.list_results(run.run_id)[0]
    quality = QualityResultService(db_session, store)
    report = quality.get_report(run.run_id)
    issue_total, issues = quality.list_issues(run.run_id)

    observations = {item.key: item for item in result.observations}
    assert completed.status == "SUCCEEDED"
    assert observations["exact_affected_record_ratio"].numerator == 2
    assert observations["exact_redundant_record_ratio"].numerator == 1
    assert observations["near_duplicate_candidate_record_count"].value >= 2
    assert report is not None
    assert report.execution_status == "SUCCEEDED"
    assert report.gate_status == "FAIL"
    assert report.metrics[0].measurement is not None
    assert report.metrics[0].measurement.key == "exact_affected_record_ratio"
    assert issue_total == len(issues)
    assert any(issue.determination == "CONFIRMED" for issue in issues)
    assert any(issue.determination == "CANDIDATE" for issue in issues)

    duplicate_evidence = next(
        item for item in result.evidence if item.kind == "DUPLICATE_GROUPS"
    )
    groups = json.loads(store.read(duplicate_evidence.artifact.uri))
    near_members = {
        sample_id
        for group in groups["near_duplicate_groups"]
        for sample_id in group["sample_ids"]
    }
    assert {"near-1", "near-2"}.issubset(near_members)

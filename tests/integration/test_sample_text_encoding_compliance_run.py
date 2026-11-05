from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
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
                    "attributes": {
                        "source_paths": ["$.content"],
                        "transform": "identity",
                    },
                }
            ],
            "annotations": [],
            "metadata": {},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=True,
    )


def test_dataset_version_to_metric_result_and_evidence_artifact(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        "\n".join(
            [
                _sample(version_id, "ok", "正常政策正文 😀"),
                _sample(version_id, "bad", "正文�疑似解码异常"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="encoding-test", modality=["text"])
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
                        "name": "BG-02-02 gate",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "0201",
                        "metric_version": "1.0.0",
                        "measurement": "compliant_record_ratio",
                        "calculator_key": "sample_text_encoding_compliance",
                        "calculator_version": "1.0.0",
                        "parameters": {"include_roles": ["article.body.plain"]},
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
        evidence = result.evidence[0]
        rows = [
            json.loads(line)
            for line in store.read(evidence.artifact.uri).decode().splitlines()
        ]

        assert executed.status == "SUCCEEDED", executed.error
        assert result.status == "OK"
        assert values["compliant_record_ratio"] == 0.5
        assert values["non_compliant_record_count"] == 1
        assert evidence.kind == "SAMPLE_TEXT_ENCODING_VIOLATIONS"
        assert rows[0]["sample_id"] == "bad"
        assert rows[0]["violation_code"] == "REPLACEMENT_CHARACTER"

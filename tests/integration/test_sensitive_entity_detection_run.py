from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
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

ROOT = Path(__file__).resolve().parents[2]
NER_PYTHON = Path("/Users/daphne/miniconda3/envs/ner-eval/bin/python")
MODEL_DIR = ROOT / "experiments/bg06_ner/models/uie-mini"
VENDOR_SRC = ROOT / "experiments/bg06_ner/vendor/uie_pytorch/src"
MODEL_SHA256 = "sha256:84dcf7310492de333a202ba03fdb9081d15f53f0b2330c9c4842da219787c0a6"


def _sample(version_id, index: int, text: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": f"synthetic-{index}",
            "contents": [
                {
                    "content_id": "body-plain",
                    "modality": "text",
                    "role": "article.body.plain",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": text, "format": "plain"},
                }
            ],
            "source": {"source_artifact_id": str(uuid4()), "source_record_id": f"synthetic-{index}"},
            "lineage": {"input_fingerprint": "sha256:" + "a" * 64},
        },
        ensure_ascii=False,
    )


@pytest.mark.skipif(
    not (NER_PYTHON.is_file() and (MODEL_DIR / "pytorch_model.bin").is_file() and VENDOR_SRC.is_dir()),
    reason="local BG-06-01 UIE assets are unavailable",
)
def test_real_uie_dataset_contract_plan_run_result_evidence_report(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    texts = [
        "联系人身份证11010519491231002X，手机号13800138000。",
        "经查，涉案人员王某某于现场接受询问。",
        "公开征求意见稿提出：请社会公众反馈意见。",
        "内部材料：涉案人员陈某。批示：核实后再报。",
        "本办法适用于北京市、朝阳区以及海淀区。",
        "普通政策正文，不含任何个人敏感要素。",
        "联系电话13912345678。",
        "工作人员赵敏负责记录。",
        "送达地址为北京市朝阳区建国路88号。",
        "经核查，涉案人员周某住址待核实。",
    ]
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text("\n".join(_sample(version_id, i, text) for i, text in enumerate(texts)) + "\n", encoding="utf-8")
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="bg-06-01-synthetic", modality=["text"]))
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
                    "field_mapping": {"mappings": [{"action": "MAP", "target": {"kind": "CONTENT", "role": "article.body.plain", "modality": "text"}}]},
                },
            )
        )
        session.flush()
        MetricRegistryService(session).sync()
        contract = QualityContractService(session).publish(
            QualityContractPublishRequest.model_validate(
                {
                    "name": "BG-06-01 synthetic observe-only",
                    "version_label": "v1",
                    "dataset_version_id": str(version_id),
                    "metric_key": "0202",
                    "metric_version": "2.0.0",
                    "measurement": "sensitive_entity_hit_count",
                    "calculator_key": "bg_06_01_sensitive_entity_detection",
                    "calculator_version": "1.0.0",
                    "parameters": {},
                    "rule": {"strategy": "OBSERVE_ONLY", "severity": "INFO", "on_missing": "SKIPPED"},
                }
            )
        ).contract
        service = EvaluationRunService(session, store)
        resolved = contract.items[0].calculation.parameters
        assert resolved["ner_python"] == str(NER_PYTHON)
        assert resolved["model_dir"] == str(MODEL_DIR)
        assert resolved["vendor_src"] == str(VENDOR_SRC)
        assert resolved["model_checksum"] == MODEL_SHA256
        run = service.create(contract.contract_version_id)
        session.commit()
        completed = service.execute(run.run_id)
        result = service.list_results(run.run_id)[0]
        stages = service.list_stage_runs(run.run_id)
        report = QualityResultService(session, store).get_report(run.run_id)
        values = {item.key: item.value for item in result.observations}
        evidence_rows = [json.loads(line) for line in store.read(result.evidence[0].artifact.uri).decode().splitlines()]

        assert completed.status == "SUCCEEDED", completed.error
        assert stages[0].status == "SUCCEEDED"
        assert result.status == "OK"
        assert values["evaluated_record_count"] == 10
        assert values["sensitive_entity_hit_count"] >= 4
        assert values["sensitive_entity_type_counts"]["MOBILE_PHONE_CN"] >= 2
        assert any("NER" in row["channels"] for row in evidence_rows)
        assert any("RULE" in row["channels"] for row in evidence_rows)
        serialized = json.dumps(evidence_rows, ensure_ascii=False)
        assert "11010519491231002X" not in serialized
        assert "13800138000" not in serialized
        assert report is not None
        assert report.execution_status == "SUCCEEDED"
        assert report.gate_status == "PASS"

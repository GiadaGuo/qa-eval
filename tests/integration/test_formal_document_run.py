import os
import shutil
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session
from test_document_calculator_acceptance import FIXTURES, MockLLM
from test_quality_result_api import (
    _client,
    _create_run,
    _fk_enforcing_engine,
    _seed_contract,
)

from apps.api.routes import evaluation_runs
from apps.api.routes.evaluation_runs import get_evaluation_dispatcher
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.llm.client import OpenAICompatibleLLMClient
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


@pytest.mark.parametrize(
    "real, filename, mode",
    [
        (False, "complete.md", "normal"),
        (False, "complete.md", "no_applicable"),
        (False, "complete.md", "resume"),
        (False, "complete.md", "corrupt"),
        (False, "complete.docx", "normal"),
        pytest.param(
            False,
            "complete.pdf",
            "normal",
            marks=pytest.mark.skipif(
                shutil.which("pdftotext") is None, reason="requires pdftotext"
            ),
        ),
        pytest.param(
            True,
            "complete.md",
            "normal",
            marks=pytest.mark.skipif(
                os.getenv("QA_EVAL_DOCUMENT_REAL") != "1",
                reason="requires a configured live LLM gateway",
            ),
        ),
    ],
)
def test_document_contract_run_and_evidence(
    tmp_path: Path, monkeypatch, real, filename, mode
):
    corrupt = mode == "corrupt"
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as db:
        data_contract = _seed_contract(db, store)
        row = db.get(QualityContractVersionModel, data_contract)
        dataset_version_id = row.target_dataset_version_id
        dataset_id = db.get(DatasetVersionModel, dataset_version_id).dataset_id
    data_run_id = _create_run(engine, store, data_contract)
    with Session(engine) as db:
        EvaluationRunService(db, store).execute(data_run_id)

    if not real:
        monkeypatch.setattr(
            OpenAICompatibleLLMClient,
            "from_settings",
            lambda settings, **options: MockLLM(),
        )
    monkeypatch.setattr(evaluation_runs, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(evaluation_runs, "get_artifact_store", lambda: store)
    from qa_eval.application.services import evaluation_run_service

    parse = evaluation_run_service.parse_document
    parsed = []

    def track_parse(data, suffix):
        parsed.append(suffix)
        return parse(data, suffix)

    monkeypatch.setattr(evaluation_run_service, "parse_document", track_parse)
    with _client(engine, store) as client:
        client.app.dependency_overrides[get_evaluation_dispatcher] = lambda: (
            lambda run_id: None
        )
        upload = client.post(
            f"/api/v1/datasets/{dataset_id}/document-versions",
            files={"file": (filename, (FIXTURES / filename).read_bytes())},
        )
        assert upload.status_code == 201, upload.text
        doc = upload.json()["id"]
        payload = {"document_version_id": doc}
        if mode == "no_applicable":
            payload["metrics"] = {
                "0101": {
                    "parameters": {"configuration": {"version": "1.1.0", "items": []}}
                }
            }
        publish = client.post(
            "/api/v1/quality-contract-versions/document",
            json=payload,
        )
        assert publish.status_code == 201, publish.text
        contract = publish.json()["contract"]
        assert contract["document_target"]["threshold"] == 90
        created = client.post(
            "/api/v1/evaluation-runs",
            json={"contract_version_id": contract["contract_version_id"]},
        )
        assert created.status_code == 202, created.text
        physical_id = created.json()["physical_plan_id"]
        frozen = client.get(f"/api/v1/evaluation-runs/physical-plans/{physical_id}")
        assert frozen.status_code == 200, frozen.text
        plan = frozen.json()
        assert plan["document_input"]["sha256"] == upload.json()["sha256"]
        assert all(
            stage["calculator"]["configuration_sha256"] for stage in plan["stages"]
        )
        run_id = UUID(created.json()["run_id"])
        if corrupt:
            uri = plan["document_input"]["uri"]
            store.local_read_path(uri).write_bytes(b"modified document")
        if mode == "resume":
            original_read = store.read

            def unavailable_evidence(uri):
                if str(run_id) in uri and uri.endswith(".jsonl"):
                    raise OSError("temporary evidence store outage")
                return original_read(uri)

            monkeypatch.setattr(store, "read", unavailable_evidence)
        evaluation_runs._execute_in_background(run_id)
        if mode == "resume":
            assert (
                client.get(
                    f"/api/v1/evaluation-runs/{run_id}/quality-report"
                ).status_code
                == 404
            )
            monkeypatch.setattr(store, "read", original_read)
            resumed = client.post(f"/api/v1/evaluation-runs/{run_id}/finalize")
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["dimensions"][0]["score"] == "20"
        with Session(engine) as db:
            run = EvaluationRunService(db, store).get(run_id)
            assert run.status == ("FAILED" if corrupt else "SUCCEEDED"), run.error
            assert str(run.document_version_id) == doc
        assert parsed == ([] if corrupt else [Path(filename).suffix])
        results = client.get(f"/api/v1/evaluation-runs/{run_id}/metric-results").json()
        assert len(results) == 4, results
        assert all(result["document_version_id"] == doc for result in results)
        native = client.get(f"/api/v1/evaluation-runs/{run_id}/calculator-results")
        assert native.status_code == 200, native.text
        assert len(native.json()) == 4
        snapshot_response = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-report"
        )
        assert snapshot_response.status_code == 200, snapshot_response.text
        snapshot = snapshot_response.json()
        assert snapshot["mode"] == "DOCUMENT" and snapshot["document_version_id"] == doc
        dimension = snapshot["dimensions"][0]
        if corrupt or mode == "no_applicable":
            assert (
                dimension["score"] is None and dimension["verdict"] == "INDETERMINATE"
            )
        else:
            assert dimension["score"] == ("100" if real else "20")
            assert len(dimension["metrics"]) == 4
            assert all(len(m["checks"]) == 5 for m in dimension["metrics"])
            combined_path = (
                f"/api/v1/evaluation-runs/{data_run_id}/combined-quality-report"
            )
            payload = {
                "document_report_id": snapshot["report_id"],
                "dataset_version_id": str(dataset_version_id),
                "applicability_confirmed": True,
            }
            assert (
                client.post(
                    combined_path, json={**payload, "dataset_version_id": str(uuid4())}
                ).status_code
                == 422
            )
            combined = client.post(combined_path, json=payload)
            assert combined.status_code == 200, combined.text
            report = combined.json()
            assert report["overall_verdict"] == "INDETERMINATE"
            assert report["dimensions"][0] == dimension
            assert report["document_snapshot"]["fingerprint"] == snapshot["fingerprint"]
            assert client.post(combined_path, json=payload).json() == report
            if mode == "normal" and filename == "complete.md":
                revision = client.post(
                    f"/api/v1/datasets/{dataset_id}/document-versions",
                    files={
                        "file": ("partial.md", (FIXTURES / "partial.md").read_bytes())
                    },
                )
                assert revision.status_code == 201 and revision.json()["id"] != doc
                newer = client.post(
                    "/api/v1/quality-contract-versions/document",
                    json={
                        "document_version_id": revision.json()["id"],
                        "threshold": 95,
                    },
                )
                assert newer.status_code == 201
                assert client.post(combined_path, json=payload).json() == report
            assert (
                client.get(f"/api/v1/quality-reports/{report['report_id']}").json()
                == report
            )
            assert (
                client.post(f"/api/v1/evaluation-runs/{run_id}/finalize").json()
                == snapshot
            )
        if corrupt:
            assert all(
                r["status"] == "ERROR" and not r["observations"] for r in results
            )
            assert all(
                r["status"] == "ERROR" and not r["statistics"] for r in native.json()
            )
            return
        if mode == "no_applicable":
            metric = next(r for r in results if r["metric"]["key"] == "0101")
            calculation = next(r for r in native.json() if r["metric"]["key"] == "0101")
            assert metric["status"] == calculation["status"] == "NOT_APPLICABLE"
            assert metric["observations"] == [] and calculation["statistics"] == {}
            return
        assert all(
            [m["value"] for m in r["observations"]]
            == ([5, 5, 1] if real else [1, 5, 0.2])
            for r in results
        )
        issues = client.get(f"/api/v1/evaluation-runs/{run_id}/quality-issues")
        assert issues.status_code == 200, issues.text
        assert issues.json()["total"] == (0 if real else 16)
        if not real:
            issue = issues.json()["issues"][0]
            detail = client.get(f"/api/v1/quality-issues/{issue['issue_id']}/evidence")
            assert detail.status_code == 200, detail.text
            assert detail.json()["rows"][0]["outcome"] == "MISSING"

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from test_quality_result_api import _client

from qa_eval.contracts.datasets import SampleRecordV2
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)
from scripts.validate_policy_document_format import (
    extract_samples,
    reviewed_samples,
    run_isolated,
)


def test_published_policy_format_native_run_and_issue_evidence(tmp_path):
    folder = next(Path("data/artifacts/datasets").glob("9ac*/01a1a9*"), None)
    if folder is None:
        pytest.skip("published policy snapshot is unavailable")
    index = next(folder.glob("sample_records.jsonl.qa-index/*/records.sqlite3"), None)
    if index is None:
        pytest.skip("published policy index is unavailable")
    real = tmp_path / "real.jsonl"
    ids = reviewed_samples()
    extract_samples(folder / "sample_records.jsonl", index, ids, real)
    source = tmp_path / "mixed.jsonl"
    first = SampleRecordV2.model_validate_json(real.open("rb").readline()).model_dump(
        mode="json"
    )
    first["sample_id"] = "synthetic-verified-n2"
    contents = {item["role"]: item for item in first["contents"]}
    contents["article.title"]["payload"]["text"] = "关于组织实施工作的通知"
    html = contents["article.body.html"]
    html["payload"]["text"] = (
        "<p>某政发[2024]9号</p><p>为促进工作落实，制定本通知。</p><p>请各单位认真组织开展相关工作。</p>"
    )
    html["attributes"].update(
        format_original_verified=True, format_standard_scope_verified=True
    )
    first["metadata"] = {"document_number": {"filenumber": "某政发[2024]9号"}}
    # Only the positive Issue-path check uses a synthetic verified-original fixture.
    with source.open("wb") as dest, real.open("rb") as actual:
        for line in actual:
            dest.write(line)
        dest.write(
            (SampleRecordV2.model_validate(first).model_dump_json() + "\n").encode()
        )
    data = run_isolated(
        tmp_path, source, folder / "dataset_manifest.json", len(ids) + 1
    )
    result = data["result"]
    stats = result["statistics"]
    assert stats["input_count"] == len(ids) + 1
    assert (
        stats["evaluable_record_count"]
        == stats["compliant_record_count"] + stats["non_compliant_record_count"]
    )
    assert (
        stats["input_count"]
        == stats["evaluable_record_count"] + stats["not_evaluated_count"]
    )
    assert stats["normative_violation_count"] == 1
    [native] = data["report"]["native_evaluations"]
    assert native["issue_count"] == native["evidence_count"] == 1
    with _client(
        create_engine(f"sqlite:///{tmp_path / 'run.sqlite3'}"),
        LocalFileSystemArtifactStore(tmp_path / "artifacts"),
    ) as client:
        issues = client.get(
            f"/api/v1/evaluation-runs/{data['run_id']}/quality-issues"
        ).json()
        assert issues["total"] == 1
        evidence_id = result["decision_artifact_refs"][0]["evidence_id"]
        assert (
            client.post(f"/api/v1/evidence/{evidence_id}/index/rebuild").status_code
            == 200
        )
        response = client.get(
            f"/api/v1/evidence/{evidence_id}/rows",
            params={"sample_id": "synthetic-verified-n2"},
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["rows"]
        assert row["attributes"]["normative_violation"] == "N2"
        [loc] = [x for x in row["locations"] if x["attributes"]["rule_id"] == "N2"]
        assert html["payload"]["text"][loc["start"] : loc["end"]] == "某政发[2024]9号"

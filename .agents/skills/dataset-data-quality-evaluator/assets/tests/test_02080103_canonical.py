from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_02080103_canonical.py"
SPEC = importlib.util.spec_from_file_location("run_02080103_canonical", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DATASET_ID = UUID("019a2a10-0000-7000-8000-000000000001")
DATASET_VERSION_ID = UUID("019a2a10-0000-7000-8000-000000000002")
NORMALIZATION_RUN_ID = UUID("019a2a10-0000-7000-8000-000000000003")
RUN_ID = UUID("019a2a10-0000-7000-8000-000000000004")
STAGE_RUN_ID = UUID("019a2a10-0000-7000-8000-000000000005")


def _write_snapshot(path: Path) -> None:
    path.mkdir()
    records = [
        {"sample_id": "a", "source": {}, "text": "相同文本", "text_is_null": False},
        {"sample_id": "b", "source": {}, "text": "相同文本", "text_is_null": False},
        {"sample_id": "c", "source": {}, "text": None, "text_is_null": True},
    ]
    raw = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for item in records
    ).encode()
    (path / "raw_snapshot.jsonl").write_bytes(raw)
    (path / "snapshot_manifest.json").write_text(
        json.dumps(
            {
                "exported_at": "2026-10-22T08:50:48Z",
                "row_count": 3,
                "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
                "ordering": {
                    "columns": ["id"],
                    "direction": "ASC",
                    "pagination": "keyset",
                },
                "consistency": {
                    "status": "REPEATABLE_READ_CONSISTENT_SNAPSHOT",
                    "table_engine": "InnoDB",
                },
            }
        ),
        encoding="utf-8",
    )


def test_canonical_pipeline_closes_counts_and_validates_schema(tmp_path: Path):
    snapshot_dir = tmp_path / "snapshot"
    output_dir = tmp_path / "canonical"
    _write_snapshot(snapshot_dir)

    result = MODULE.run(
        snapshot_dir,
        output_dir,
        dataset_id=DATASET_ID,
        dataset_version_id=DATASET_VERSION_ID,
        normalization_run_id=NORMALIZATION_RUN_ID,
        run_id=RUN_ID,
        stage_run_id=STAGE_RUN_ID,
    )

    assert result["status"] == "OK"
    assert UUID(result["metric_result_id"]).version == 7
    assert all(UUID(item["evidence_id"]).version == 7 for item in result["evidence"])
    assert all(
        UUID(item["artifact"]["artifact_id"]).version == 7
        for item in result["evidence"]
    )
    assert result["population"] == {
        "input_count": 3,
        "evaluated_count": 2,
        "excluded_count": 1,
        "exclusions": [{"reason": "EMPTY_TEXT", "count": 1}],
    }
    observations = {item["key"]: item for item in result["observations"]}
    assert observations["exact_affected_record_ratio"]["value"] == 1.0
    assert observations["exact_redundant_record_ratio"]["value"] == 0.5

    samples = [
        json.loads(line)
        for line in (output_dir / "sample_records.jsonl").read_text().splitlines()
    ]
    assert samples[2]["contents"][0]["presence"] == "NULL"
    assert "payload" not in samples[2]["contents"][0]
    assert (output_dir / "rejected_records.jsonl").read_text() == ""

    repository_root = Path(__file__).parents[5]
    for schema_path, payload_path in [
        (
            repository_root / "schemas/qa-eval/dataset-manifest/2.0.schema.json",
            output_dir / "dataset_manifest.json",
        ),
        (
            repository_root / "schemas/qa-eval/metric-result/1.0.schema.json",
            output_dir / "metrics/02080103-v2.0.0/metric_result.json",
        ),
    ]:
        schema = json.loads(schema_path.read_text())
        payload = json.loads(payload_path.read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)

    context = json.loads((output_dir / "local_execution_context.json").read_text())
    assert context["authority"] == "LOCAL_VALIDATION"
    assert context["not_a_platform_run"] is True

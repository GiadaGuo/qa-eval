from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from qa_eval.contracts.quality import IssueIndexV1, QualityIssueV1, QualityReportV1

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "complete_02080103_evaluation.py"
SPEC = importlib.util.spec_from_file_location(
    "complete_02080103_evaluation", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

RUN_ID = "019a2a10-0000-7000-8000-000000000104"
DATASET_VERSION_ID = "019a2a10-0000-7000-8000-000000000102"
METRIC_RESULT_ID = "019a2a10-0000-7000-8000-000000000203"
EVIDENCE_ID = "019a2a10-0000-7000-8000-000000000205"
EVIDENCE_ARTIFACT_ID = "019a2a10-0000-7000-8000-000000000206"
CONTRACT_VERSION_ID = "019a2a10-0000-7000-8000-000000000202"
NOW = "2026-10-27T00:00:00Z"


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    canonical_dir = tmp_path / "canonical"
    metric_dir = canonical_dir / "metrics/02080103-v2.0.0"
    metric_dir.mkdir(parents=True)
    groups = {
        "metric_code": "02080103",
        "algorithm_version": "2.0.0",
        "groups": [
            {
                "group_id": f"sha256:{'a' * 64}",
                "normalized_sha256": "a" * 64,
                "record_count": 2,
                "representative_sample_id": "a",
                "sample_ids": ["a", "b"],
            }
        ],
        "near_duplicate_groups": [
            {"group_id": "near:000001", "record_count": 2, "sample_ids": ["a", "c"]}
        ],
        "near_duplicate_pairs": [],
        "paragraph_groups": [],
    }
    evidence_path = metric_dir / "duplicate_groups.json"
    evidence_path.write_text(json.dumps(groups), encoding="utf-8")
    checksum = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    metric_result = {
        "schema_version": "1.0",
        "metric_result_id": METRIC_RESULT_ID,
        "run_id": RUN_ID,
        "stage_run_id": "019a2a10-0000-7000-8000-000000000105",
        "dataset_version_id": DATASET_VERSION_ID,
        "metric": {"key": "02080103", "version": "1.0.0"},
        "subject": {"scope": "DATASET", "id": DATASET_VERSION_ID},
        "status": "OK",
        "observations": [
            {
                "key": "exact_redundant_record_ratio",
                "value": 1 / 3,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": 1,
                "denominator": 3,
            },
            {
                "key": "near_duplicate_candidate_record_count",
                "value": 2,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ],
        "population": {
            "input_count": 3,
            "evaluated_count": 3,
            "excluded_count": 0,
            "exclusions": [],
        },
        "semantic_coverage": {
            "level": "PARTIAL",
            "covered": ["exact", "near candidate"],
            "excluded": ["semantic duplicate"],
            "limitations": [],
        },
        "evidence": [
            {
                "evidence_id": EVIDENCE_ID,
                "kind": "DUPLICATE_GROUPS",
                "schema_ref": "qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0",
                "artifact": {
                    "artifact_id": EVIDENCE_ARTIFACT_ID,
                    "kind": "METRIC_EVIDENCE",
                    "uri": "metrics/02080103-v2.0.0/duplicate_groups.json",
                    "format": "json",
                    "media_type": "application/json",
                    "schema_ref": "qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0",
                    "size_bytes": evidence_path.stat().st_size,
                    "record_count": 1,
                    "checksum": {"algorithm": "sha256", "value": checksum},
                },
                "subject_scope": "SAMPLE",
                "summary": {"record_count": 1, "error_count": 0, "attributes": {}},
                "redaction": {
                    "contains_raw_content": False,
                    "policy": "hashes-and-identifiers-only",
                },
            }
        ],
        "created_at": NOW,
    }
    (metric_dir / "metric_result.json").write_text(
        json.dumps(metric_result), encoding="utf-8"
    )
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_id": "019a2a10-0000-7000-8000-000000000201",
                "contract_version_id": CONTRACT_VERSION_ID,
                "version_label": "1.0.0",
                "name": "user-input-contract",
                "status": "PUBLISHED",
                "applicability": {
                    "modalities": ["text"],
                    "task_types": ["generic_corpus"],
                },
                "items": [
                    {
                        "key": "exact-gate",
                        "metric": {
                            "key": "02080103",
                            "version": "1.0.0",
                            "measurement": "exact_redundant_record_ratio",
                        },
                        "scope": "DATASET",
                        "calculation": {},
                        "rule": {"operator": "LTE", "threshold": 0.5},
                        "severity": "ERROR",
                        "on_missing": "UNKNOWN",
                    }
                ],
                "gate": {
                    "unknown_policy": "FAIL",
                    "fail_on_severities": ["ERROR", "CRITICAL"],
                },
                "fingerprint": f"sha256:{'b' * 64}",
                "published_at": NOW,
            }
        ),
        encoding="utf-8",
    )
    return canonical_dir, contract_path


def test_executor_reaches_canonical_quality_report(tmp_path: Path):
    canonical_dir, contract_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "quality"
    summary = MODULE.complete(
        canonical_dir,
        contract_path,
        output_dir,
        execution_status="SUCCEEDED",
        execution_authority="LOCAL_VALIDATION",
    )

    assert summary["rule_evaluation_count"] == 1
    assert summary["confirmed_issue_count"] == 1
    assert summary["candidate_issue_count"] == 2
    assert summary["gate_status"] == "PASS"

    issues = [
        QualityIssueV1.model_validate_json(line)
        for line in (output_dir / "issues.jsonl").read_text().splitlines()
    ]
    assert len(issues) == 3
    assert {item.determination for item in issues} == {"CONFIRMED", "CANDIDATE"}
    issue_index = IssueIndexV1.model_validate_json(
        (output_dir / "issue_index.json").read_text()
    )
    assert issue_index.artifact.record_count == 3
    report_data = json.loads((output_dir / "quality_report.json").read_text())
    report = QualityReportV1.model_validate(report_data)
    assert report.execution_status == "SUCCEEDED"
    assert report.gate_status == "PASS"
    assert report.coverage.evaluated == 1
    assert report.issue_summary.confirmed_count == 1
    assert report.issue_summary.candidate_count == 2

    repository_root = Path(__file__).parents[5]
    schema = json.loads(
        (repository_root / "schemas/qa-eval/quality-report/1.0.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report_data)


def test_executor_rejects_tampered_evidence(tmp_path: Path):
    canonical_dir, contract_path = _write_inputs(tmp_path)
    evidence_path = canonical_dir / "metrics/02080103-v2.0.0/duplicate_groups.json"
    evidence_path.write_text("{}", encoding="utf-8")
    try:
        MODULE.complete(
            canonical_dir,
            contract_path,
            tmp_path / "quality",
            execution_status="SUCCEEDED",
            execution_authority="LOCAL_VALIDATION",
        )
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("tampered evidence must be rejected")

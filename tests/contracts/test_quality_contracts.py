import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)
from scripts.contracts.generate_json_schemas import SCHEMA_ROOT

DATASET_VERSION_ID = "019a2a10-0000-7000-8000-000000000102"
CONTRACT_ID = "019a2a10-0000-7000-8000-000000000201"
CONTRACT_VERSION_ID = "019a2a10-0000-7000-8000-000000000202"
RUN_ID = "019a2a10-0000-7000-8000-000000000104"
METRIC_RESULT_ID = "019a2a10-0000-7000-8000-000000000203"
RULE_EVALUATION_ID = "019a2a10-0000-7000-8000-000000000204"
EVIDENCE_ID = "019a2a10-0000-7000-8000-000000000205"
ISSUE_ID = "019a2a10-0000-7000-8000-000000000206"
ISSUE_INDEX_ID = "019a2a10-0000-7000-8000-000000000207"
ISSUE_ARTIFACT_ID = "019a2a10-0000-7000-8000-000000000208"
REPORT_ID = "019a2a10-0000-7000-8000-000000000209"
NOW = "2026-10-27T00:00:00Z"
SHA256 = "b" * 64


def quality_contract() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "contract_id": CONTRACT_ID,
        "contract_version_id": CONTRACT_VERSION_ID,
        "version_label": "1.0.0",
        "name": "policy-article-user-contract",
        "status": "PUBLISHED",
        "applicability": {
            "modalities": ["text"],
            "task_types": ["generic_corpus"],
        },
        "items": [
            {
                "key": "exact_duplicate_gate",
                "metric": {
                    "key": "02080103",
                    "version": "1.0.0",
                    "measurement": "exact_redundant_record_ratio",
                },
                "scope": "DATASET",
                "calculation": {
                    "parameters": {},
                    "implementation_constraints": {"deterministic": True},
                },
                "rule": {"operator": "LTE", "threshold": 0.01},
                "severity": "ERROR",
                "on_missing": "UNKNOWN",
            }
        ],
        "gate": {
            "unknown_policy": "FAIL",
            "fail_on_severities": ["ERROR", "CRITICAL"],
        },
        "fingerprint": f"sha256:{SHA256}",
        "published_at": NOW,
    }


def rule_evaluation() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "rule_evaluation_id": RULE_EVALUATION_ID,
        "run_id": RUN_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "contract_version_id": CONTRACT_VERSION_ID,
        "contract_item_key": "exact_duplicate_gate",
        "metric_result_id": METRIC_RESULT_ID,
        "metric": {
            "key": "02080103",
            "version": "1.0.0",
            "measurement": "exact_redundant_record_ratio",
        },
        "subject": {"scope": "DATASET", "id": DATASET_VERSION_ID},
        "status": "PASS",
        "observed": {
            "value": 45 / 14928,
            "value_type": "NUMBER",
            "unit": "ratio",
        },
        "rule": {"operator": "LTE", "expected": 0.01},
        "reason_code": "THRESHOLD_SATISFIED",
        "evaluated_at": NOW,
    }


def quality_issue() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issue_id": ISSUE_ID,
        "run_id": RUN_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "source": {
            "metric_result_id": METRIC_RESULT_ID,
            "metric_key": "02080103",
            "metric_version": "1.0.0",
            "measurement": "exact_redundant_record_ratio",
            "rule_evaluation_id": None,
        },
        "detector": {
            "key": "exact_duplicate_redundant_sample",
            "version": "1.0.0",
        },
        "category": "DUPLICATE_CONTENT",
        "code": "EXACT_DUPLICATE_REDUNDANT",
        "determination": "CONFIRMED",
        "severity": "WARNING",
        "scope": "SAMPLE",
        "subject": {"type": "SAMPLE", "sample_id": "duplicate-sample"},
        "related_subjects": [
            {
                "relation": "DUPLICATE_OF",
                "subject": {"type": "SAMPLE", "sample_id": "representative-sample"},
            }
        ],
        "message": "该样本与重复组代表样本的规范化正文完全相同。",
        "evidence": [
            {
                "evidence_id": EVIDENCE_ID,
                "locator": {
                    "group_id": f"sha256:{SHA256}",
                    "normalized_sha256": SHA256,
                },
            }
        ],
        "dedup_key": f"sha256:{SHA256}:duplicate-sample",
        "status": "OPEN",
        "created_at": NOW,
        "updated_at": NOW,
    }


def issue_index() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issue_index_id": ISSUE_INDEX_ID,
        "run_id": RUN_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "artifact": {
            "artifact_id": ISSUE_ARTIFACT_ID,
            "kind": "ISSUE_INDEX",
            "uri": "runs/local/issues.jsonl",
            "format": "jsonl",
            "media_type": "application/x-ndjson",
            "schema_ref": "qa-eval://schemas/quality-issue/1.0.schema.json",
            "size_bytes": 123,
            "record_count": 2,
            "checksum": {"algorithm": "sha256", "value": SHA256},
        },
        "supported_filters": [
            "metric_key",
            "code",
            "determination",
            "severity",
            "scope",
            "sample_id",
        ],
        "summary": {"confirmed_count": 1, "candidate_count": 1},
    }


def quality_report() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "report_id": REPORT_ID,
        "run_id": RUN_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "contract_version_id": CONTRACT_VERSION_ID,
        "generator": {"key": "qa-eval-report", "version": "1.0.0"},
        "execution_status": "SUCCEEDED",
        "gate_status": "PASS",
        "coverage": {
            "evaluated": 1,
            "not_applicable": 0,
            "missing": 0,
            "error": 0,
            "not_evaluated": 30,
        },
        "dimensions": [
            {
                "key": "DATA_QUALITY",
                "status": "PARTIAL_COVERAGE",
                "evaluated_metric_count": 1,
                "total_metric_count": 31,
            }
        ],
        "metrics": [
            {
                "metric": {
                    "key": "02080103",
                    "version": "1.0.0",
                    "name": "重复内容程度",
                },
                "metric_result_id": METRIC_RESULT_ID,
                "status": "OK",
                "observations": [
                    {
                        "key": "exact_redundant_record_ratio",
                        "value": 45 / 14928,
                        "unit": "ratio",
                    }
                ],
                "rule_evaluations": [
                    {
                        "rule_evaluation_id": RULE_EVALUATION_ID,
                        "contract_item_key": "exact_duplicate_gate",
                        "status": "PASS",
                    }
                ],
                "issue_summary": {
                    "confirmed_count": 45,
                    "candidate_count": 7517,
                    "by_code": {
                        "EXACT_DUPLICATE_REDUNDANT": 45,
                        "NEAR_DUPLICATE_CANDIDATE": 7517,
                    },
                    "by_scope": {"SAMPLE": 7562},
                },
                "issue_index_id": ISSUE_INDEX_ID,
                "evidence_ids": [EVIDENCE_ID],
            }
        ],
        "issue_summary": {
            "confirmed_count": 45,
            "candidate_count": 7517,
            "open_count": 7562,
        },
        "rendered_artifact_ids": [],
        "generated_at": NOW,
    }


CASES: list[tuple[Path, type[BaseModel], Any]] = [
    (
        Path("quality-contract/1.0.schema.json"),
        QualityContractVersionV1,
        quality_contract,
    ),
    (Path("rule-evaluation/1.0.schema.json"), RuleEvaluationV1, rule_evaluation),
    (Path("quality-issue/1.0.schema.json"), QualityIssueV1, quality_issue),
    (Path("issue-index/1.0.schema.json"), IssueIndexV1, issue_index),
    (Path("quality-report/1.0.schema.json"), QualityReportV1, quality_report),
]


def validator(relative_path: Path) -> Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / relative_path).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(("relative_path", "model", "factory"), CASES)
def test_valid_payload_passes_pydantic_and_json_schema(relative_path, model, factory):
    payload = factory()
    parsed = model.model_validate(payload)
    validator(relative_path).validate(parsed.model_dump(mode="json", exclude_none=True))


@pytest.mark.parametrize(("relative_path", "model", "factory"), CASES)
def test_unknown_field_fails_pydantic_and_json_schema(relative_path, model, factory):
    payload = deepcopy(factory())
    payload["unexpected"] = True
    with pytest.raises(PydanticValidationError):
        model.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        validator(relative_path).validate(payload)


def test_contract_threshold_is_required_user_input():
    payload = quality_contract()
    del payload["items"][0]["rule"]["threshold"]
    with pytest.raises(PydanticValidationError):
        QualityContractVersionV1.model_validate(payload)


def test_contract_item_keys_must_be_unique():
    payload = quality_contract()
    payload["items"].append(deepcopy(payload["items"][0]))
    with pytest.raises(PydanticValidationError, match="must be unique"):
        QualityContractVersionV1.model_validate(payload)


def test_unknown_rule_evaluation_has_no_observed_value():
    payload = rule_evaluation()
    payload["status"] = "UNKNOWN"
    payload["reason_code"] = "METRIC_MISSING"
    payload.pop("observed")
    RuleEvaluationV1.model_validate(payload)
    payload["observed"] = {"value": 0, "value_type": "NUMBER", "unit": "ratio"}
    with pytest.raises(PydanticValidationError, match="must not contain observed"):
        RuleEvaluationV1.model_validate(payload)


def test_issue_scope_must_match_subject_type():
    payload = quality_issue()
    payload["scope"] = "PAIR"
    with pytest.raises(PydanticValidationError, match="scope must match"):
        QualityIssueV1.model_validate(payload)


def test_issue_index_counts_must_match_artifact():
    payload = issue_index()
    payload["artifact"]["record_count"] = 3
    with pytest.raises(PydanticValidationError, match="record_count must equal"):
        IssueIndexV1.model_validate(payload)


def test_report_metric_sections_are_unique():
    payload = quality_report()
    payload["metrics"].append(deepcopy(payload["metrics"][0]))
    with pytest.raises(PydanticValidationError, match="must be unique"):
        QualityReportV1.model_validate(payload)

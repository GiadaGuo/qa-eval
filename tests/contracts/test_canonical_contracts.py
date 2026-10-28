import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.datasets import (
    DatasetManifestV2,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.document import DocumentQualityReportV1
from qa_eval.contracts.metrics import MetricResultV1
from scripts.contracts.generate_json_schemas import (
    REPOSITORY_ROOT,
    SCHEMA_ROOT,
    SCHEMAS,
    render_schema,
)

DATASET_ID = "550e8400-e29b-41d4-a716-446655440000"
DATASET_VERSION_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
ARTIFACT_ID = "018f47a2-9b3c-7def-8123-456789abcdef"
RUN_ID = "018f47a2-9b3c-7def-8123-456789abc001"
STAGE_RUN_ID = "018f47a2-9b3c-7def-8123-456789abc002"
RESULT_ID = "018f47a2-9b3c-7def-8123-456789abc003"
NORMALIZATION_RUN_ID = "018f47a2-9b3c-7def-8123-456789abc004"
DOCUMENT_TASK_ID = "018f47a2-9b3c-7def-8123-456789abc005"
DOCUMENT_REPORT_ID = "018f47a2-9b3c-7def-8123-456789abc006"
DOCUMENT_VERSION_ID = "018f47a2-9b3c-7def-8123-456789abc007"
SHA256 = "a" * 64
NOW = "2026-10-22T08:50:48Z"


def artifact() -> dict[str, Any]:
    return {
        "artifact_id": ARTIFACT_ID,
        "kind": "RAW_DATA",
        "uri": "data/raw/source.jsonl",
        "format": "jsonl",
        "media_type": "application/x-ndjson",
        "schema_ref": "qa-eval://schemas/raw/mysql-row/1.0",
        "size_bytes": 123,
        "record_count": 1,
        "checksum": {"algorithm": "sha256", "value": SHA256},
    }


def manifest() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "dataset_id": DATASET_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "name": "policy.jhsjk_article",
        "modalities": ["text"],
        "source_snapshot": {
            "source_type": "mysql",
            "source_uri": "mysql://policy/jhsjk_article",
            "captured_at": NOW,
            "consistency": "REPEATABLE_READ_CONSISTENT_SNAPSHOT",
            "record_count": 1,
            "details": {},
        },
        "artifacts": [artifact()],
        "field_mapping": {
            "schema_version": "2.0",
            "adapter": {"key": "mysql_record", "version": "2.0.0"},
            "mappings": [
                {
                    "source_path": "$.plain_text_content",
                    "action": "MAP",
                    "target": {
                        "kind": "CONTENT",
                        "role": "article.body.plain",
                        "modality": "text",
                    },
                    "value_type": "string",
                    "presence_policy": "PRESERVE",
                    "required_for_normalization": False,
                }
            ],
        },
        "declared_context": {"languages": ["zh"], "task_types": ["generic_corpus"]},
        "governance": {},
        "created_at": NOW,
    }


def sample() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "dataset_version_id": DATASET_VERSION_ID,
        "sample_id": "42",
        "contents": [
            {
                "content_id": "body-plain",
                "modality": "text",
                "role": "article.body.plain",
                "presence": "PRESENT",
                "payload": {
                    "type": "inline_text",
                    "text": "文章正文",
                    "format": "plain",
                    "language": "zh",
                },
                "attributes": {},
            }
        ],
        "annotations": [],
        "metadata": {},
        "source": {
            "source_artifact_id": ARTIFACT_ID,
            "source_record_id": "42",
            "source_row": 0,
            "locator": "mysql://policy/jhsjk_article#id=42",
            "attributes": {},
        },
        "lineage": {
            "input_fingerprint": f"sha256:{SHA256}",
            "transforms": [
                {
                    "key": "mysql_record",
                    "version": "2.0.0",
                    "config_fingerprint": f"sha256:{SHA256}",
                }
            ],
        },
    }


def rejected_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "dataset_version_id": DATASET_VERSION_ID,
        "normalization_run_id": NORMALIZATION_RUN_ID,
        "source": {
            "source_artifact_id": ARTIFACT_ID,
            "source_row": 42,
            "source_record_id": None,
        },
        "error": {
            "code": "MISSING_SAMPLE_ID",
            "message": "id is missing",
            "json_pointer": "$.id",
            "retryable": False,
        },
        "redacted_excerpt": None,
        "created_at": NOW,
    }


def metric_result() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "metric_result_id": RESULT_ID,
        "run_id": RUN_ID,
        "stage_run_id": STAGE_RUN_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "metric": {"key": "02080103", "version": "1.0.0"},
        "subject": {"scope": "DATASET", "id": DATASET_VERSION_ID},
        "status": "OK",
        "observations": [
            {
                "key": "exact_affected_record_ratio",
                "value": 89 / 14928,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": 89,
                "denominator": 14928,
            }
        ],
        "population": {
            "input_count": 14960,
            "evaluated_count": 14928,
            "excluded_count": 32,
            "exclusions": [{"reason": "EMPTY_TEXT", "count": 32}],
        },
        "semantic_coverage": {
            "level": "PARTIAL",
            "covered": ["normalized exact duplicate"],
            "excluded": ["semantic duplicate"],
            "limitations": [],
        },
        "evidence": [],
        "created_at": NOW,
    }


def document_quality_report() -> dict[str, Any]:
    def item(index: int, status: str = "SATISFIED") -> dict[str, Any]:
        return {
            "item_code": f"0101-0{index}",
            "item_name": f"检查项{index}",
            "status": status,
            "document_evidence": []
            if status == "MISSING"
            else [
                {
                    "line_start": 1,
                    "line_end": 1,
                    "excerpt": "# 数据集说明",
                }
            ],
            "inspected_scope": "检查全文",
            "expected_section": "六、技术支持" if status == "MISSING" else None,
            "reason": "判定依据",
            "suggestion": "修改建议",
            "fixability": "REQUIRES_HUMAN_INPUT",
        }

    return {
        "schema_version": "1.0",
        "report_id": DOCUMENT_REPORT_ID,
        "task_id": DOCUMENT_TASK_ID,
        "document_version_id": DOCUMENT_VERSION_ID,
        "dataset_id": DATASET_ID,
        "document": {
            "version": "v1",
            "filename": "readme.md",
            "sha256": SHA256,
            "line_count": 12,
            "size_bytes": 1024,
        },
        "evaluation_standard": {
            "name": "数据集说明文档质量评测标准",
            "version": "1.0.0",
            "sha256": SHA256,
        },
        "evaluator": {
            "version": "1.0.0",
            "model": "test-model",
            "prompt_version": "1.0.0",
            "parameters": {"temperature": 0.1},
            "structured_output_validation": True,
        },
        "execution_status": "PARTIAL_SUCCEEDED",
        "metrics": [
            {
                "metric": {"key": "0101", "version": "1.0.0"},
                "metric_name": "基本信息完整性",
                "status": "OK",
                "observations": [
                    {
                        "key": "satisfied_count",
                        "value": 2,
                        "value_type": "NUMBER",
                        "numerator": 2,
                        "denominator": 5,
                    },
                    {
                        "key": "required_count",
                        "value": 5,
                        "value_type": "NUMBER",
                    },
                    {
                        "key": "score",
                        "value": 40.0,
                        "value_type": "NUMBER",
                        "unit": "percent",
                        "numerator": 2,
                        "denominator": 5,
                    },
                ],
                "items": [
                    item(1),
                    item(2),
                    item(3, "INSUFFICIENT"),
                    item(4, "MISSING"),
                    item(5, "UNVERIFIABLE"),
                ],
            },
            {
                "metric": {"key": "0102", "version": "1.0.0"},
                "metric_name": "内容特征完整性",
                "status": "ERROR",
                "observations": [],
                "items": [],
                "error": "校验重试耗尽",
            },
        ],
        "overall": {
            "score": None,
            "scoring_policy": "等权平均（平台口径）",
            "explanation": "指标 0102 为 ERROR，总分不可计算",
        },
        "missing_items": [
            {
                "item_code": "0101-04",
                "item_name": "检查项4",
                "expected_section": "六、技术支持",
            }
        ],
        "recommendations": [
            {
                "item_code": "0101-03",
                "suggestion": "修改建议",
                "fixability": "REQUIRES_HUMAN_INPUT",
            },
            {
                "item_code": "0101-04",
                "suggestion": "修改建议",
                "fixability": "REQUIRES_HUMAN_INPUT",
            },
        ],
        "limitations": [],
        "generated_at": NOW,
    }


CASES: list[tuple[Path, type[BaseModel], Any]] = [
    (Path("artifact-ref/1.0.schema.json"), ArtifactRef, artifact),
    (Path("dataset-manifest/2.0.schema.json"), DatasetManifestV2, manifest),
    (Path("sample-record/2.0.schema.json"), SampleRecordV2, sample),
    (Path("rejected-record/1.0.schema.json"), RejectedRecordV1, rejected_record),
    (Path("metric-result/1.0.schema.json"), MetricResultV1, metric_result),
    (
        Path("document-quality-report/1.0.schema.json"),
        DocumentQualityReportV1,
        document_quality_report,
    ),
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


def test_sample_semantic_invariants_are_enforced_by_pydantic():
    payload = sample()
    payload["contents"][0]["presence"] = "NULL"
    with pytest.raises(PydanticValidationError, match="must omit payload"):
        SampleRecordV2.model_validate(payload)


def test_metric_population_must_close():
    payload = metric_result()
    payload["population"]["excluded_count"] = 31
    with pytest.raises(PydanticValidationError, match="must equal input_count"):
        MetricResultV1.model_validate(payload)


def test_platform_timestamp_requires_timezone():
    payload = rejected_record()
    payload["created_at"] = datetime(2026, 10, 22, 8, 50, 48)
    with pytest.raises(PydanticValidationError, match="must include a timezone"):
        RejectedRecordV1.model_validate(payload)
    payload["created_at"] = datetime(2026, 10, 22, 8, 50, 48, tzinfo=UTC)
    RejectedRecordV1.model_validate(payload)


@pytest.mark.parametrize(("relative_path", "model"), SCHEMAS.items())
def test_checked_in_schema_matches_pydantic(relative_path, model):
    expected = render_schema(relative_path, model)
    actual = (SCHEMA_ROOT / relative_path).read_text()
    assert actual == expected, f"regenerate {relative_path}"


def test_schema_generator_targets_repository_schema_directory():
    assert SCHEMA_ROOT == REPOSITORY_ROOT / "schemas" / "qa-eval"

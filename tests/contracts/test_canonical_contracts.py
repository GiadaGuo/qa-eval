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


CASES: list[tuple[Path, type[BaseModel], Any]] = [
    (Path("artifact-ref/1.0.schema.json"), ArtifactRef, artifact),
    (Path("dataset-manifest/2.0.schema.json"), DatasetManifestV2, manifest),
    (Path("sample-record/2.0.schema.json"), SampleRecordV2, sample),
    (Path("rejected-record/1.0.schema.json"), RejectedRecordV1, rejected_record),
    (Path("metric-result/1.0.schema.json"), MetricResultV1, metric_result),
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

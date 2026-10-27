"""Run the jhsjk_article 02080103 prototype through canonical wire contracts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import secrets
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.datasets import (
    DatasetManifestV2,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.metrics import MetricResultV1

CALCULATOR_PATH = Path(__file__).with_name("calculate_text_duplicates.py")
CALCULATOR_SPEC = importlib.util.spec_from_file_location(
    "calculate_text_duplicates", CALCULATOR_PATH
)
assert CALCULATOR_SPEC is not None and CALCULATOR_SPEC.loader is not None
CALCULATOR = importlib.util.module_from_spec(CALCULATOR_SPEC)
CALCULATOR_SPEC.loader.exec_module(CALCULATOR)

CONTENT_ROLE = "article.body.plain"
METRIC_VERSION = "1.0.0"
CALCULATOR_VERSION = "2.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _uuid7() -> UUID:
    """Generate an RFC 9562 UUIDv7 on Python versions without uuid.uuid7."""

    unix_ms = int(time.time_ns() // 1_000_000)
    if unix_ms >= 1 << 48:
        raise OverflowError("UUIDv7 timestamp exceeds 48 bits")
    value = unix_ms << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return UUID(int=value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl_record(destination, model: SampleRecordV2 | RejectedRecordV1) -> None:
    value = model.model_dump(mode="json", exclude_none=True)
    destination.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _normalize_snapshot(
    raw_path: Path,
    normalized_path: Path,
    rejected_path: Path,
    *,
    dataset_version_id: UUID,
    normalization_run_id: UUID,
    raw_artifact_id: UUID,
    input_fingerprint: str,
    created_at: datetime,
) -> tuple[int, int]:
    normalized_count = rejected_count = 0
    config_fingerprint = hashlib.sha256(
        b"jhsjk_article:id->sample_id;plain_text_content->article.body.plain:v2"
    ).hexdigest()
    with (
        raw_path.open(encoding="utf-8") as source,
        normalized_path.open("w", encoding="utf-8") as normalized_output,
        rejected_path.open("w", encoding="utf-8") as rejected_output,
    ):
        for source_row, line in enumerate(source):
            try:
                raw = json.loads(line)
                sample_id = raw["sample_id"]
                text = raw["text"]
                if not isinstance(sample_id, str) or not sample_id:
                    raise ValueError("MISSING_SAMPLE_ID")
                if text is not None and not isinstance(text, str):
                    raise ValueError("TEXT_NOT_STRING_OR_NULL")
                if raw.get("text_is_null") is not (text is None):
                    raise ValueError("NULL_MARKER_MISMATCH")

                content: dict[str, Any] = {
                    "content_id": "body-plain",
                    "modality": "text",
                    "role": CONTENT_ROLE,
                    "presence": "NULL" if text is None else "PRESENT",
                    "attributes": {},
                }
                if text is not None:
                    content["payload"] = {
                        "type": "inline_text",
                        "text": text,
                        "format": "plain",
                        "language": "zh",
                    }
                sample = SampleRecordV2.model_validate(
                    {
                        "schema_version": "2.0",
                        "dataset_version_id": dataset_version_id,
                        "sample_id": sample_id,
                        "contents": [content],
                        "annotations": [],
                        "metadata": {},
                        "source": {
                            "source_artifact_id": raw_artifact_id,
                            "source_record_id": sample_id,
                            "source_row": source_row,
                            "locator": f"mysql://policy/jhsjk_article#id={sample_id}",
                            "attributes": {},
                        },
                        "lineage": {
                            "input_fingerprint": f"sha256:{input_fingerprint}",
                            "transforms": [
                                {
                                    "key": "jhsjk_article_snapshot_migration",
                                    "version": "1.0.0",
                                    "config_fingerprint": f"sha256:{config_fingerprint}",
                                }
                            ],
                        },
                    }
                )
                _write_jsonl_record(normalized_output, sample)
                normalized_count += 1
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                rejected = RejectedRecordV1.model_validate(
                    {
                        "schema_version": "1.0",
                        "dataset_version_id": dataset_version_id,
                        "normalization_run_id": normalization_run_id,
                        "source": {
                            "source_artifact_id": raw_artifact_id,
                            "source_row": source_row,
                            "source_record_id": None,
                        },
                        "error": {
                            "code": type(exc).__name__.upper(),
                            "message": str(exc),
                            "json_pointer": None,
                            "retryable": False,
                        },
                        "redacted_excerpt": None,
                        "created_at": created_at,
                    }
                )
                _write_jsonl_record(rejected_output, rejected)
                rejected_count += 1
    return normalized_count, rejected_count


def run(
    snapshot_dir: Path,
    output_dir: Path,
    *,
    dataset_id: UUID,
    dataset_version_id: UUID,
    normalization_run_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    raw_path = snapshot_dir / "raw_snapshot.jsonl"
    legacy_manifest = json.loads(
        (snapshot_dir / "snapshot_manifest.json").read_text(encoding="utf-8")
    )
    raw_checksum = _sha256(raw_path)
    expected_checksum = legacy_manifest["sha256"].removeprefix("sha256:")
    if raw_checksum != expected_checksum:
        raise ValueError("raw snapshot checksum does not match snapshot_manifest.json")
    created_at = datetime.now(UTC)
    raw_artifact_id = _uuid7()

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent)
    )
    try:
        normalized_path = staging_dir / "sample_records.jsonl"
        rejected_path = staging_dir / "rejected_records.jsonl"
        normalized_count, rejected_count = _normalize_snapshot(
            raw_path,
            normalized_path,
            rejected_path,
            dataset_version_id=dataset_version_id,
            normalization_run_id=normalization_run_id,
            raw_artifact_id=raw_artifact_id,
            input_fingerprint=raw_checksum,
            created_at=created_at,
        )
        if normalized_count + rejected_count != legacy_manifest["row_count"]:
            raise RuntimeError("normalization counts do not close to source row count")

        normalized_checksum = _sha256(normalized_path)
        rejected_checksum = _sha256(rejected_path)
        normalized_artifact_id = _uuid7()
        rejected_artifact_id = _uuid7()
        artifacts = [
            ArtifactRef.model_validate(
                {
                    "artifact_id": raw_artifact_id,
                    "kind": "RAW_DATA",
                    "uri": str(raw_path),
                    "format": "jsonl",
                    "media_type": "application/x-ndjson",
                    "schema_ref": "qa-eval://schemas/legacy/jhsjk-article-snapshot/1.0",
                    "size_bytes": raw_path.stat().st_size,
                    "record_count": legacy_manifest["row_count"],
                    "checksum": {"algorithm": "sha256", "value": raw_checksum},
                }
            ),
            ArtifactRef.model_validate(
                {
                    "artifact_id": normalized_artifact_id,
                    "kind": "NORMALIZED_DATA",
                    "uri": "sample_records.jsonl",
                    "format": "jsonl",
                    "media_type": "application/x-ndjson",
                    "schema_ref": "qa-eval://schemas/sample-record/2.0.schema.json",
                    "size_bytes": normalized_path.stat().st_size,
                    "record_count": normalized_count,
                    "checksum": {"algorithm": "sha256", "value": normalized_checksum},
                }
            ),
            ArtifactRef.model_validate(
                {
                    "artifact_id": rejected_artifact_id,
                    "kind": "REJECTED_RECORDS",
                    "uri": "rejected_records.jsonl",
                    "format": "jsonl",
                    "media_type": "application/x-ndjson",
                    "schema_ref": "qa-eval://schemas/rejected-record/1.0.schema.json",
                    "size_bytes": rejected_path.stat().st_size,
                    "record_count": rejected_count,
                    "checksum": {"algorithm": "sha256", "value": rejected_checksum},
                }
            ),
        ]
        manifest = DatasetManifestV2.model_validate(
            {
                "schema_version": "2.0",
                "dataset_id": dataset_id,
                "dataset_version_id": dataset_version_id,
                "name": "policy.jhsjk_article",
                "modalities": ["text"],
                "source_snapshot": {
                    "source_type": "mysql",
                    "source_uri": "mysql://policy/jhsjk_article",
                    "captured_at": legacy_manifest["exported_at"],
                    "consistency": legacy_manifest["consistency"]["status"],
                    "record_count": legacy_manifest["row_count"],
                    "details": {
                        "table_engine": legacy_manifest["consistency"]["table_engine"],
                        "ordering": legacy_manifest["ordering"],
                    },
                },
                "artifacts": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in artifacts
                ],
                "field_mapping": {
                    "schema_version": "2.0",
                    "adapter": {
                        "key": "jhsjk_article_snapshot_migration",
                        "version": "1.0.0",
                    },
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
                                "role": CONTENT_ROLE,
                                "modality": "text",
                            },
                            "value_type": "string|null",
                            "presence_policy": "PRESERVE",
                            "required_for_normalization": False,
                        },
                        {"source_path": "$.text_is_null", "action": "IGNORE"},
                        {"source_path": "$.source", "action": "PASSTHROUGH"},
                    ],
                },
                "declared_context": {
                    "languages": ["zh"],
                    "task_types": ["generic_corpus"],
                },
                "governance": {},
                "created_at": created_at,
            }
        )
        _write_json(
            staging_dir / "dataset_manifest.json",
            manifest.model_dump(mode="json", exclude_none=True),
        )

        metric_dir = staging_dir / "metrics" / "02080103-v2.0.0"
        legacy_result = CALCULATOR.calculate(
            normalized_path,
            metric_dir,
            input_format="sample-record-v2",
            content_role=CONTENT_ROLE,
        )
        legacy_result_path = metric_dir / "metric_result.json"
        legacy_result_path.rename(metric_dir / "calculator_result.json")

        evidence_specs = [
            (
                metric_dir / "sample_features.jsonl",
                "SAMPLE_FEATURES",
                "qa-eval://schemas/evidence/02080103-sample-features/2.0.0",
                legacy_result["input_record_count"],
            ),
            (
                metric_dir / "duplicate_groups.json",
                "DUPLICATE_GROUPS",
                "qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0",
                1,
            ),
        ]
        evidence = []
        for path, kind, schema_ref, record_count in evidence_specs:
            checksum = _sha256(path)
            artifact = ArtifactRef.model_validate(
                {
                    "artifact_id": _uuid7(),
                    "kind": "METRIC_EVIDENCE",
                    "uri": str(path.relative_to(staging_dir)),
                    "format": path.suffix.removeprefix("."),
                    "media_type": (
                        "application/x-ndjson"
                        if path.suffix == ".jsonl"
                        else "application/json"
                    ),
                    "schema_ref": schema_ref,
                    "size_bytes": path.stat().st_size,
                    "record_count": record_count,
                    "checksum": {"algorithm": "sha256", "value": checksum},
                }
            )
            evidence.append(
                {
                    "evidence_id": _uuid7(),
                    "kind": kind,
                    "schema_ref": schema_ref,
                    "artifact": artifact.model_dump(mode="json", exclude_none=True),
                    "subject_scope": "SAMPLE",
                    "summary": {
                        "record_count": record_count,
                        "error_count": legacy_result["anomaly_count"],
                        "attributes": {},
                    },
                    "redaction": {
                        "contains_raw_content": False,
                        "policy": "hashes-and-identifiers-only",
                    },
                }
            )

        observations = [
            {
                "key": "exact_affected_record_ratio",
                "value": legacy_result["affected_record_ratio"],
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": legacy_result["duplicate_record_count"],
                "denominator": legacy_result["valid_record_count"],
            },
            {
                "key": "exact_redundant_record_ratio",
                "value": legacy_result["redundant_record_ratio"],
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": legacy_result["redundant_record_count"],
                "denominator": legacy_result["valid_record_count"],
            },
            {
                "key": "char_ngram_repetition_ratio",
                "value": legacy_result["char_rep_ratio"],
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": legacy_result["char_repeated_ngram_frequency_sum"],
                "denominator": legacy_result["char_ngram_total_count"],
            },
            {
                "key": "word_ngram_repetition_ratio",
                "value": legacy_result["word_rep_ratio"],
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": legacy_result["word_repeated_ngram_frequency_sum"],
                "denominator": legacy_result["word_ngram_total_count"],
            },
            {
                "key": "near_duplicate_candidate_record_count",
                "value": legacy_result["minhash"]["near_duplicate_record_count"],
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "paragraph_affected_ratio",
                "value": legacy_result["paragraph_affected_ratio"],
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": legacy_result["repeated_paragraph_count"],
                "denominator": legacy_result["paragraph_total_count"],
            },
        ]
        metric_result = MetricResultV1.model_validate(
            {
                "schema_version": "1.0",
                "metric_result_id": _uuid7(),
                "run_id": run_id,
                "stage_run_id": stage_run_id,
                "dataset_version_id": dataset_version_id,
                "metric": {"key": "02080103", "version": METRIC_VERSION},
                "subject": {"scope": "DATASET", "id": str(dataset_version_id)},
                "status": "OK",
                "observations": observations,
                "population": {
                    "input_count": legacy_result["input_record_count"],
                    "evaluated_count": legacy_result["valid_record_count"],
                    "excluded_count": legacy_result["anomaly_count"],
                    "exclusions": [
                        {
                            "reason": "EMPTY_TEXT",
                            "count": legacy_result["anomaly_count"],
                        }
                    ],
                },
                "semantic_coverage": {
                    "level": "PARTIAL",
                    "covered": [
                        "normalized exact duplicate",
                        "character n-gram repetition",
                        "lexical token n-gram repetition",
                        "MinHash+LSH near-duplicate candidates",
                        "paragraph hash duplicate",
                    ],
                    "excluded": ["semantic duplicate", "sentence semantic duplicate"],
                    "limitations": [
                        "near-duplicate outputs are candidates, not confirmed issues"
                    ],
                },
                "evidence": evidence,
                "created_at": created_at,
            }
        )
        _write_json(
            metric_dir / "metric_result.json",
            metric_result.model_dump(mode="json", exclude_none=True),
        )
        _write_json(
            staging_dir / "local_execution_context.json",
            {
                "authority": "LOCAL_VALIDATION",
                "not_a_platform_run": True,
                "dataset_id": str(dataset_id),
                "dataset_version_id": str(dataset_version_id),
                "normalization_run_id": str(normalization_run_id),
                "run_id": str(run_id),
                "stage_run_id": str(stage_run_id),
                "calculator": {"key": "text_duplicates", "version": CALCULATOR_VERSION},
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
            },
        )
        staging_dir.rename(output_dir)
        return metric_result.model_dump(mode="json", exclude_none=True)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--dataset-id", type=UUID, required=True)
    parser.add_argument("--dataset-version-id", type=UUID, required=True)
    parser.add_argument("--normalization-run-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--stage-run-id", type=UUID, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run(
        args.snapshot_dir,
        args.output_dir,
        dataset_id=args.dataset_id,
        dataset_version_id=args.dataset_version_id,
        normalization_run_id=args.normalization_run_id,
        run_id=args.run_id,
        stage_run_id=args.stage_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

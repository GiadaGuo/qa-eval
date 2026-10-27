# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 将 policy.jhsjk_article 的 legacy snapshot JSONL 标准化为
# SampleRecord 2.0 / RejectedRecord 1.0，并生成 DatasetManifest 2.0、
# NormalizationRun 1.0 和 DatasetProfile 1.0。
#
# 这是闭环 2 的最小适配器：字段映射固定，不暴露通用字段映射 UI。
# ====================================================

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from qa_eval.contracts import (
    ArtifactRef,
    DatasetManifestV2,
    NormalizationRunV1,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.uuid7 import uuid7

ADAPTER_KEY = "jhsjk_article_snapshot_migration"
ADAPTER_VERSION = "1.0.0"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl_record(destination, model: SampleRecordV2 | RejectedRecordV1) -> None:
    value = model.model_dump(mode="json", exclude_none=True)
    destination.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class NormalizationResult:
    normalized_count: int
    rejected_count: int
    raw_artifact: ArtifactRef
    normalized_artifact: ArtifactRef
    rejected_artifact: ArtifactRef
    manifest: DatasetManifestV2
    normalization_run: NormalizationRunV1


class JhsjkArticleJsonlNormalizer:
    """将 jhsjk_article legacy snapshot JSONL 标准化为 canonical artifacts。"""

    def __init__(self, artifact_store: ArtifactStore):
        self._store = artifact_store

    def normalize(
        self,
        raw_path: Path,
        *,
        dataset_id: UUID,
        dataset_version_id: UUID,
        source_uri: str = "mysql://policy/jhsjk_article",
        captured_at: datetime | None = None,
        field_mapping: FieldMappingSpecV2,
    ) -> NormalizationResult:
        if captured_at is None:
            captured_at = datetime.now(UTC)

        raw_checksum = _sha256_file(raw_path)
        raw_artifact_id = uuid7()
        normalization_run_id = uuid7()
        normalized_artifact_id = uuid7()
        rejected_artifact_id = uuid7()
        created_at = datetime.now(UTC)

        config_fingerprint = hashlib.sha256(
            b"jhsjk_article:id->sample_id;plain_text_content->article.body.plain:v2"
        ).hexdigest()

        relative_prefix = f"datasets/{dataset_id}/{dataset_version_id}"
        raw_relative = f"{relative_prefix}/raw_snapshot.jsonl"
        normalized_relative = f"{relative_prefix}/sample_records.jsonl"
        rejected_relative = f"{relative_prefix}/rejected_records.jsonl"
        manifest_relative = f"{relative_prefix}/dataset_manifest.json"

        # 1. commit raw artifact
        raw_staging = self._store.prepare_write(raw_relative)
        raw_staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(raw_path, raw_staging)
        raw_stored = self._store.commit(raw_relative, sha256=raw_checksum)

        # 2. normalize to staging files
        normalized_staging = self._store.prepare_write(normalized_relative)
        rejected_staging = self._store.prepare_write(rejected_relative)

        source_row = -1
        normalized_count = 0
        rejected_count = 0

        with (
            raw_path.open(encoding="utf-8") as source,
            normalized_staging.open("w", encoding="utf-8") as normalized_output,
            rejected_staging.open("w", encoding="utf-8") as rejected_output,
        ):
            for source_row, line in enumerate(source):
                try:
                    raw = json.loads(line)
                    sample_rule = next(
                        rule
                        for rule in field_mapping.mappings
                        if rule.action == "MAP"
                        and rule.target
                        and rule.target.kind == "SAMPLE_ID"
                    )
                    sample_field = sample_rule.source_path.removeprefix("$.")
                    sample_id = raw.get(sample_field)
                    if sample_id is None or str(sample_id).strip() == "":
                        raise ValueError("MISSING_SAMPLE_ID")
                    sample_id = str(sample_id)
                    contents: list[dict[str, Any]] = []
                    annotation_values: dict[str, Any] = {}
                    metadata: dict[str, Any] = {}
                    source_attributes: dict[str, Any] = {}
                    for rule in field_mapping.mappings:
                        source_field = rule.source_path.removeprefix("$.")
                        if rule.action == "IGNORE":
                            continue
                        if rule.action == "PASSTHROUGH":
                            metadata.setdefault("passthrough", {})[source_field] = (
                                raw.get(source_field)
                            )
                            continue
                        if rule.target is None or rule.target.kind == "SAMPLE_ID":
                            continue
                        present = source_field in raw
                        value = raw.get(source_field)
                        target_name = rule.target.name or source_field
                        if rule.target.kind == "CONTENT":
                            role = rule.target.role or source_field
                            content: dict[str, Any] = {
                                "content_id": source_field,
                                "modality": rule.target.modality or "text",
                                "role": role,
                                "presence": "MISSING"
                                if not present
                                else "NULL"
                                if value is None
                                else "PRESENT",
                                "attributes": {},
                            }
                            if present and value is not None:
                                if not isinstance(value, str):
                                    raise ValueError(
                                        f"CONTENT_NOT_STRING:{source_field}"
                                    )
                                content["payload"] = {
                                    "type": "inline_text",
                                    "text": value,
                                    "format": "html"
                                    if role.endswith(".html")
                                    else "plain",
                                    "language": "zh",
                                }
                            contents.append(content)
                        elif rule.target.kind == "METADATA":
                            metadata[target_name] = value
                        elif rule.target.kind == "TEMPORAL":
                            metadata[target_name] = {
                                "value": value,
                                "precision": "SECOND",
                                "source_timezone": None,
                                "timezone_status": "UNKNOWN",
                            }
                        elif rule.target.kind == "SOURCE_ATTRIBUTE":
                            source_attributes[target_name] = value
                        elif rule.target.kind == "ANNOTATION":
                            annotation_values[target_name] = value
                    if not contents:
                        raise ValueError("NO_CONTENT_MAPPED")
                    annotations = [
                        {
                            "annotation_id": f"imported-{name}",
                            "task": {"key": name, "version": "1.0"},
                            "targets": [{"content_id": contents[0]["content_id"]}],
                            "value": {"type": "structured", "data": value},
                            "provenance": {
                                "method": "IMPORTED",
                                "annotator_ids": [],
                                "tool": "jsonl_mapping",
                            },
                            "status": "FINAL",
                            "attributes": {"source_field": name},
                        }
                        for name, value in annotation_values.items()
                    ]

                    sample = SampleRecordV2.model_validate(
                        {
                            "schema_version": "2.0",
                            "dataset_version_id": dataset_version_id,
                            "sample_id": sample_id,
                            "contents": contents,
                            "annotations": annotations,
                            "metadata": metadata,
                            "source": {
                                "source_artifact_id": raw_artifact_id,
                                "source_record_id": sample_id,
                                "source_row": source_row,
                                "locator": f"{source_uri}#id={sample_id}",
                                "attributes": source_attributes,
                            },
                            "lineage": {
                                "input_fingerprint": f"sha256:{raw_checksum}",
                                "transforms": [
                                    {
                                        "key": ADAPTER_KEY,
                                        "version": ADAPTER_VERSION,
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

        input_count = source_row + 1
        if normalized_count + rejected_count != input_count:
            raise RuntimeError("normalization counts do not close to source row count")

        # 3. commit normalized / rejected artifacts
        normalized_stored = self._store.commit(normalized_relative)
        rejected_stored = self._store.commit(rejected_relative)

        # 4. build manifest
        raw_artifact = ArtifactRef.model_validate(
            {
                "artifact_id": raw_artifact_id,
                "kind": "RAW_DATA",
                "uri": raw_stored.uri,
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/legacy/jhsjk-article-snapshot/1.0",
                "size_bytes": raw_stored.size_bytes,
                "record_count": input_count,
                "checksum": {
                    "algorithm": "sha256",
                    "value": raw_stored.sha256,
                },
            }
        )
        normalized_artifact = ArtifactRef.model_validate(
            {
                "artifact_id": normalized_artifact_id,
                "kind": "NORMALIZED_DATA",
                "uri": normalized_stored.uri,
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/sample-record/2.0.schema.json",
                "size_bytes": normalized_stored.size_bytes,
                "record_count": normalized_count,
                "checksum": {
                    "algorithm": "sha256",
                    "value": normalized_stored.sha256,
                },
            }
        )
        rejected_artifact = ArtifactRef.model_validate(
            {
                "artifact_id": rejected_artifact_id,
                "kind": "REJECTED_RECORDS",
                "uri": rejected_stored.uri,
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/rejected-record/1.0.schema.json",
                "size_bytes": rejected_stored.size_bytes,
                "record_count": rejected_count,
                "checksum": {
                    "algorithm": "sha256",
                    "value": rejected_stored.sha256,
                },
            }
        )

        manifest = DatasetManifestV2.model_validate(
            {
                "schema_version": "2.0",
                "dataset_id": dataset_id,
                "dataset_version_id": dataset_version_id,
                "name": "policy.jhsjk_article",
                "modalities": ["text"],
                "source_snapshot": {
                    "source_type": "jsonl_upload",
                    "source_uri": source_uri,
                    "captured_at": captured_at,
                    "consistency": "UPLOADED_SNAPSHOT",
                    "record_count": input_count,
                    "details": {
                        "upload_format": "jsonl",
                        "original_checksum": f"sha256:{raw_checksum}",
                    },
                },
                "artifacts": [
                    raw_artifact.model_dump(mode="json", exclude_none=True),
                    normalized_artifact.model_dump(mode="json", exclude_none=True),
                    rejected_artifact.model_dump(mode="json", exclude_none=True),
                ],
                "field_mapping": field_mapping.model_dump(mode="json"),
                "declared_context": {
                    "languages": ["zh"],
                    "task_types": ["generic_corpus"],
                    "attributes": {},
                },
                "governance": {},
                "created_at": created_at,
            }
        )

        # 5. build normalization run
        normalization_run = NormalizationRunV1.model_validate(
            {
                "schema_version": "1.0",
                "normalization_run_id": normalization_run_id,
                "dataset_version_id": dataset_version_id,
                "adapter": {
                    "key": ADAPTER_KEY,
                    "version": ADAPTER_VERSION,
                    "code_digest": f"sha256:{config_fingerprint}",
                    "config_fingerprint": f"sha256:{config_fingerprint}",
                },
                "input_artifact_ids": [raw_artifact_id],
                "status": "SUCCEEDED",
                "counts": {
                    "input": input_count,
                    "normalized": normalized_count,
                    "rejected": rejected_count,
                },
                "output_artifact_ids": [
                    normalized_artifact_id,
                    rejected_artifact_id,
                ],
                "started_at": created_at,
                "finished_at": datetime.now(UTC),
                "error": None,
            }
        )

        # 6. commit manifest; profiling is a separate application component.
        manifest_staging = self._store.prepare_write(manifest_relative)
        _write_json(manifest_staging, manifest.model_dump(mode="json"))
        self._store.commit(manifest_relative)

        return NormalizationResult(
            normalized_count=normalized_count,
            rejected_count=rejected_count,
            raw_artifact=raw_artifact,
            normalized_artifact=normalized_artifact,
            rejected_artifact=rejected_artifact,
            manifest=manifest,
            normalization_run=normalization_run,
        )

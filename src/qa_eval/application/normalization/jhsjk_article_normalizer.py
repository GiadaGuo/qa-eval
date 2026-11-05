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
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from qa_eval.application.normalization.source_readers import source_reader
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
ADAPTER_VERSION = "2.0.0"


class _PlainTextHTMLParser(HTMLParser):
    _break_tags: ClassVar[set[str]] = {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._ignored += 1
        elif tag in self._break_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
        elif tag in self._break_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in self.parts_as_text().splitlines()]
        return "\n".join(line for line in lines if line)

    def parts_as_text(self) -> str:
        return unescape("".join(self.parts))


def _html_to_text(value: str) -> str:
    parser = _PlainTextHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.text()


def _set_nested(target: dict[str, Any], dotted_name: str, value: Any) -> None:
    parts = dotted_name.split(".")
    current = target
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise TypeError(f"TARGET_PATH_CONFLICT:{dotted_name}")
        current = child
    current[parts[-1]] = value


def _temporal_value(value: Any) -> tuple[dict[str, Any] | None, Any | None]:
    if value is None:
        return None, None
    original = value
    parsed: datetime | date
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit() and len(value.strip()) >= 11
    ):
        try:
            parsed = datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("INVALID_EPOCH_MILLISECONDS") from exc
    elif isinstance(value, str):
        raw = value.strip()
        if raw == "None":
            return None, original
        try:
            parsed = (
                date.fromisoformat(raw)
                if len(raw) == 10
                else datetime.fromisoformat(raw)
            )
        except ValueError:
            try:
                parsed = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("INVALID_TEMPORAL_VALUE") from exc
    else:
        raise ValueError("INVALID_TEMPORAL_VALUE")
    if isinstance(parsed, datetime):
        known = parsed.tzinfo is not None and parsed.utcoffset() is not None
        result = {
            "value": parsed.isoformat(),
            "precision": "SECOND",
            "source_timezone": "UTC"
            if known and parsed.utcoffset().total_seconds() == 0
            else None,
            "timezone_status": "KNOWN" if known else "UNKNOWN",
        }
    else:
        result = {
            "value": parsed.isoformat(),
            "precision": "DAY",
            "source_timezone": None,
            "timezone_status": "NOT_APPLICABLE",
        }
    return result, original if result["value"] != str(original) else None


def _mapping_fingerprint(
    field_mapping: FieldMappingSpecV2,
    adapter_parameters: dict[str, Any] | None = None,
) -> str:
    payload = {
        "mapping": field_mapping.model_dump(mode="json"),
        "adapter": {"key": ADAPTER_KEY, "version": ADAPTER_VERSION},
        "adapter_parameters": adapter_parameters or {},
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _policy_postprocess(
    metadata: dict[str, Any], source_attributes: dict[str, Any]
) -> None:
    warnings: list[dict[str, Any]] = []
    publisher = metadata.get("publisher")
    if isinstance(publisher, dict):
        organization = publisher.get("organization")
        publishunit = publisher.get("publishunit")
        publisher["value"] = organization or publishunit
        if organization and publishunit and organization != publishunit:
            warnings.append(
                {
                    "code": "PUBLISHER_INCONSISTENT",
                    "source_paths": ["$.organization", "$.publishunit"],
                    "message": "organization and publishunit differ; organization was selected",
                }
            )
    document_number = metadata.get("document_number")
    if isinstance(document_number, dict):
        document_number["value"] = document_number.get(
            "filenumber"
        ) or document_number.get("issue")
    policy_level = metadata.get("policy_level")
    if isinstance(policy_level, dict):
        raw = policy_level.get("level") or policy_level.get("zccjfl")
        labels = {
            "1": "NATIONAL",
            "2": "MUNICIPAL",
            "3": "DISTRICT_COUNTY",
            "国家级": "NATIONAL",
            "省级": "PROVINCIAL",
            "市级": "MUNICIPAL",
            "区县级": "DISTRICT_COUNTY",
        }
        policy_level["value"] = labels.get(str(raw), raw)
    business = metadata.get("business")
    if isinstance(business, dict) and "is_public" in business:
        raw_public = business["is_public"]
        if raw_public in (1, "1", "true", "True", True):
            business["is_public"] = True
        elif raw_public in (0, "0", "false", "False", False):
            business["is_public"] = False
    attachment_parts = metadata.get("attachments")
    if isinstance(attachment_parts, dict):
        names = attachment_parts.get("name")
        urls = attachment_parts.get("url")
        names = names if isinstance(names, list) else [names] if names else []
        urls = urls if isinstance(urls, list) else [urls] if urls else []
        count = max(len(names), len(urls))
        metadata["attachments"] = [
            {
                **({"name": names[index]} if index < len(names) else {}),
                **({"url": urls[index]} if index < len(urls) else {}),
            }
            for index in range(count)
        ]
        if len(names) != len(urls):
            warnings.append(
                {
                    "code": "ATTACHMENT_PARTS_MISMATCH",
                    "source_paths": ["$.attachname", "$.attachurl"],
                    "message": "attachment names and URLs have different counts",
                }
            )
    relation_parts = metadata.get("relations")
    if isinstance(relation_parts, dict):
        relations: list[dict[str, Any]] = []
        title = relation_parts.get("interpretation_title")
        recommended_url = relation_parts.get("recommended_url")
        if title or recommended_url:
            relations.append(
                {
                    "type": "POLICY_INTERPRETATION",
                    **({"title": title} if title else {}),
                    **({"url": recommended_url} if recommended_url else {}),
                }
            )
        video_url = relation_parts.get("video_interpretation_url")
        if video_url:
            relations.append({"type": "VIDEO_INTERPRETATION", "url": video_url})
        metadata["relations"] = relations
    fallback = source_attributes.pop("fallback_url", None)
    if isinstance(fallback, dict):
        source_attributes["fallback_urls"] = [
            value for value in fallback.values() if value
        ]
    if warnings:
        metadata.setdefault("normalization", {}).setdefault("warnings", []).extend(
            warnings
        )


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
        input_format: str = "jsonl",
        mapping_provenance: dict[str, Any] | None = None,
        adapter_parameters: dict[str, Any] | None = None,
    ) -> NormalizationResult:
        if captured_at is None:
            captured_at = datetime.now(UTC)

        raw_checksum = _sha256_file(raw_path)
        raw_artifact_id = uuid7()
        normalization_run_id = uuid7()
        normalized_artifact_id = uuid7()
        rejected_artifact_id = uuid7()
        created_at = datetime.now(UTC)

        config_fingerprint = _mapping_fingerprint(field_mapping, adapter_parameters)
        reader = source_reader(raw_path, input_format)
        description = reader.describe()
        schema_fingerprint = hashlib.sha256(
            description.schema_fingerprint_input.encode()
        ).hexdigest()

        relative_prefix = f"datasets/{dataset_id}/{dataset_version_id}"
        raw_relative = f"{relative_prefix}/raw_snapshot.{description.format}"
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

        input_count = description.record_count
        normalized_count = 0
        rejected_count = 0

        with (
            normalized_staging.open("w", encoding="utf-8") as normalized_output,
            rejected_staging.open("w", encoding="utf-8") as rejected_output,
        ):
            for source_row, raw in reader.iter_rows():
                sample_id: str | None = None
                try:
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
                    source_values: dict[str, Any] = {}
                    for rule in field_mapping.mappings:
                        source_field = rule.source_path.removeprefix("$.")
                        if rule.action == "IGNORE":
                            continue
                        if rule.action == "PASSTHROUGH":
                            metadata.setdefault("extra", {})[source_field] = raw.get(
                                source_field
                            )
                            continue
                        if rule.target is None or rule.target.kind == "SAMPLE_ID":
                            continue
                        present = source_field in raw
                        value = raw.get(source_field)
                        if (not present or value is None) and (
                            rule.required_for_normalization
                            or rule.presence_policy == "REJECT_MISSING"
                        ):
                            raise ValueError(f"REQUIRED_FIELD_MISSING:{source_field}")
                        if value is None and rule.presence_policy == "DROP_NULL":
                            continue
                        target_name = rule.target.name or source_field
                        if rule.target.kind == "CONTENT":
                            role = rule.target.role or source_field
                            content_id = rule.target.name or source_field
                            content: dict[str, Any] = {
                                "content_id": content_id,
                                "modality": rule.target.modality or "text",
                                "role": role,
                                "presence": "MISSING"
                                if not present
                                else "NULL"
                                if value is None
                                else "PRESENT",
                                "attributes": {"source_paths": [rule.source_path]},
                            }
                            if present and value is not None:
                                if not isinstance(value, str):
                                    raise ValueError(
                                        f"CONTENT_NOT_STRING:{source_field}"
                                    )
                                is_plain_from_html = (
                                    role == "article.body.plain"
                                    and source_field == "content"
                                )
                                text_value = (
                                    _html_to_text(value)
                                    if is_plain_from_html
                                    else value
                                )
                                content["payload"] = {
                                    "type": "inline_text",
                                    "text": text_value,
                                    "format": "html"
                                    if role.endswith(".html")
                                    else "plain",
                                    "language": "zh",
                                }
                                content["attributes"]["transform"] = (
                                    "html_to_complete_plain_text"
                                    if is_plain_from_html
                                    else "identity"
                                )
                                if is_plain_from_html:
                                    content["attributes"]["transform_version"] = "1.0.0"
                            contents.append(content)
                        elif rule.target.kind == "METADATA":
                            _set_nested(metadata, target_name, value)
                        elif rule.target.kind == "TEMPORAL":
                            if field_mapping.adapter.key == "jhsjk_policy_article":
                                temporal, original = _temporal_value(value)
                                temporal_target = f"temporal.{target_name}"
                            else:
                                temporal = {
                                    "value": value,
                                    "precision": "SECOND",
                                    "source_timezone": None,
                                    "timezone_status": "UNKNOWN",
                                }
                                original = None
                                temporal_target = target_name
                            _set_nested(metadata, temporal_target, temporal)
                            if original is not None:
                                source_values[source_field] = original
                        elif rule.target.kind == "SOURCE_ATTRIBUTE":
                            _set_nested(source_attributes, target_name, value)
                        elif rule.target.kind == "ANNOTATION":
                            annotation_values[target_name] = value
                    if not contents:
                        raise ValueError("NO_CONTENT_MAPPED")
                    if source_values:
                        metadata.setdefault("normalization", {})["source_values"] = (
                            source_values
                        )
                    if field_mapping.adapter.key == "jhsjk_policy_article":
                        _policy_postprocess(metadata, source_attributes)
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
                except (KeyError, StopIteration, TypeError, ValueError) as exc:
                    message = str(exc) or type(exc).__name__
                    code = message.split(":", 1)[0]
                    rejected = RejectedRecordV1.model_validate(
                        {
                            "schema_version": "1.0",
                            "dataset_version_id": dataset_version_id,
                            "normalization_run_id": normalization_run_id,
                            "source": {
                                "source_artifact_id": raw_artifact_id,
                                "source_row": source_row,
                                "source_record_id": sample_id,
                            },
                            "error": {
                                "code": code,
                                "message": message,
                                "json_pointer": None,
                                "retryable": False,
                            },
                            "redacted_excerpt": {
                                key: value
                                for key, value in raw.items()
                                if key not in {"content"}
                            },
                            "created_at": created_at,
                        }
                    )
                    _write_jsonl_record(rejected_output, rejected)
                    rejected_count += 1

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
                "format": description.format,
                "media_type": description.media_type,
                "schema_ref": f"qa-eval://schemas/raw/{description.format}/{schema_fingerprint}",
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
                    "source_type": description.source_type,
                    "source_uri": source_uri,
                    "captured_at": captured_at,
                    "consistency": "UPLOADED_SNAPSHOT",
                    "record_count": input_count,
                    "details": {
                        "upload_format": description.format,
                        "original_checksum": f"sha256:{raw_checksum}",
                        "schema_fingerprint": f"sha256:{schema_fingerprint}",
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
                    "attributes": {
                        "mapping_provenance": mapping_provenance
                        or {"source": "USER_CONFIGURED"}
                    },
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

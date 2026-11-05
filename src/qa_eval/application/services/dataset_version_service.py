# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# DatasetVersion 应用服务：创建版本、上传 raw JSONL、发布版本并生成
# DatasetManifest 2.0 / SampleRecord 2.0 / DatasetProfile 1.0。
# ====================================================

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from qa_eval.application.mapping_templates import MappingTemplateResolver
from qa_eval.application.normalization.jhsjk_article_normalizer import (
    JhsjkArticleJsonlNormalizer,
)
from qa_eval.application.normalization.source_readers import source_reader
from qa_eval.application.profiling.base_dataset_profiler import BaseDatasetProfiler
from qa_eval.contracts import DatasetManifestV2, DatasetProfileV1, SampleRecordV2
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset_version import (
    DatasetVersion,
    DatasetVersionStatus,
)
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.domain.repositories.dataset_profile_repository import (
    DatasetProfileRepository,
)
from qa_eval.domain.repositories.dataset_repository import DatasetRepository
from qa_eval.domain.repositories.dataset_version_repository import (
    DatasetVersionRepository,
)
from qa_eval.infrastructure.uuid7 import uuid7

logger = logging.getLogger(__name__)


class DatasetVersionService:
    """协调 DatasetVersion 生命周期与 canonical 产物生成。"""

    def __init__(
        self,
        dataset_repository: DatasetRepository,
        version_repository: DatasetVersionRepository,
        profile_repository: DatasetProfileRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._dataset_repository = dataset_repository
        self._version_repository = version_repository
        self._profile_repository = profile_repository
        self._artifact_store = artifact_store
        self._normalizer = JhsjkArticleJsonlNormalizer(artifact_store)
        self._profiler = BaseDatasetProfiler()
        self._mapping_resolver = MappingTemplateResolver()

    def save_field_mapping(
        self, version_id: UUID, mapping: FieldMappingSpecV2
    ) -> FieldMappingSpecV2:
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        if version.status != DatasetVersionStatus.DRAFT:
            raise ValueError("Only DRAFT versions can change field mapping")
        refs = self._version_repository.get_artifact_refs(version_id) or {}
        if "raw_file" not in refs:
            raise ValueError(
                "Upload a raw JSONL or Parquet file before configuring field mapping"
            )
        sample_ids = [
            rule
            for rule in mapping.mappings
            if rule.action == "MAP" and rule.target and rule.target.kind == "SAMPLE_ID"
        ]
        contents = [
            rule
            for rule in mapping.mappings
            if rule.action == "MAP" and rule.target and rule.target.kind == "CONTENT"
        ]
        if len(sample_ids) != 1:
            raise ValueError("Field mapping requires exactly one SAMPLE_ID")
        if not contents:
            raise ValueError("Field mapping requires at least one CONTENT field")
        content_ids = [
            rule.target.name or rule.source_path.removeprefix("$.")
            for rule in contents
            if rule.target is not None
        ]
        if len(content_ids) != len(set(content_ids)):
            raise ValueError("CONTENT mappings must produce unique content IDs")
        refs["field_mapping"] = mapping.model_dump(mode="json")
        recommendation = refs.get("mapping_recommendation")
        if recommendation is None and version.status == DatasetVersionStatus.DRAFT:
            refs["mapping_provenance"] = {"source": "USER_CONFIGURED"}
        else:
            recommended_mapping = FieldMappingSpecV2.model_validate(
                recommendation["mapping"]
            )
            refs["mapping_provenance"] = {
                **recommendation["provenance"],
                "customized": mapping != recommended_mapping,
            }
        self._version_repository.update_artifact_refs(version_id, refs)
        return mapping

    def get_field_mapping(self, version_id: UUID) -> FieldMappingSpecV2 | None:
        refs = self._version_repository.get_artifact_refs(version_id) or {}
        document = refs.get("field_mapping")
        return None if document is None else FieldMappingSpecV2.model_validate(document)

    def create_version(
        self,
        dataset_id: UUID,
        version_label: str,
    ) -> DatasetVersion:
        """为指定数据集创建 DRAFT 版本。"""
        if self._dataset_repository.get_by_id(dataset_id) is None:
            raise ValueError(f"Dataset not found: {dataset_id}")

        version = DatasetVersion(
            id=uuid7(),
            dataset_id=dataset_id,
            version=version_label,
            status=DatasetVersionStatus.DRAFT,
        )
        return self._version_repository.add(version)

    def upload_raw_file(
        self,
        version_id: UUID,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        """兼容小文件 bytes 上传；内部仍使用流式路径实现。"""
        format_name = self._format_from_filename(filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{format_name}") as tmp:
            tmp.write(content)
            source_path = Path(tmp.name)
        try:
            return self.upload_raw_path(version_id, filename, source_path)
        finally:
            source_path.unlink(missing_ok=True)

    def upload_raw_path(
        self,
        version_id: UUID,
        filename: str,
        source_path: Path,
    ) -> dict[str, Any]:
        """从本地临时文件流式校验并提交 raw JSONL/Parquet。"""
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        if version.status != DatasetVersionStatus.DRAFT:
            raise ValueError("Only DRAFT versions can accept file uploads")

        format_name = self._format_from_filename(filename)
        reader = source_reader(source_path, format_name)
        description = reader.describe()
        preview = reader.preview(5)

        relative_path = f"datasets/{version.dataset_id}/{version_id}/raw/{filename}"
        stored = self._artifact_store.put_file(relative_path, source_path)

        artifact_refs = self._version_repository.get_artifact_refs(version_id) or {}
        # A mapping belongs to the exact uploaded snapshot. Re-uploading invalidates it.
        artifact_refs.pop("field_mapping", None)
        artifact_refs.pop("mapping_provenance", None)
        resolved = self._mapping_resolver.resolve(
            [field["name"] for field in description.fields]
        )
        if resolved is None:
            artifact_refs.pop("mapping_recommendation", None)
        else:
            artifact_refs["mapping_recommendation"] = {
                "mapping": resolved.mapping.model_dump(mode="json"),
                "provenance": resolved.provenance(),
                "adapter_parameters": resolved.adapter_parameters,
            }
        artifact_refs["raw_file"] = {
            "uri": stored.uri,
            "sha256": stored.sha256,
            "filename": filename,
            "size_bytes": stored.size_bytes,
            "format": description.format,
            "media_type": description.media_type,
            "record_count": description.record_count,
            "schema_fingerprint": "sha256:"
            + hashlib.sha256(description.schema_fingerprint_input.encode()).hexdigest(),
            "fields": description.fields,
            "preview": preview,
        }
        content_fingerprint = f"sha256:{stored.sha256}"
        self._version_repository.update_artifact_refs(
            version_id,
            artifact_refs,
            content_fingerprint=content_fingerprint,
        )

        return {
            "version_id": str(version_id),
            "filename": filename,
            "uri": stored.uri,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "validated_format": "jsonl_objects"
            if description.format == "jsonl"
            else "parquet",
            "record_count": description.record_count,
            "schema_fingerprint": artifact_refs["raw_file"]["schema_fingerprint"],
        }

    def inspect_uploaded_source(
        self,
        version_id: UUID,
        *,
        preview_limit: int = 5,
        max_text_length: int = 300,
    ) -> dict[str, Any]:
        """Infer fields from the complete uploaded JSONL and return a bounded preview."""
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        refs = self._version_repository.get_artifact_refs(version_id) or {}
        raw_ref = refs.get("raw_file")
        if raw_ref is None:
            raise ValueError("No raw file uploaded for this version")

        fields = raw_ref.get("fields")
        cached_preview = raw_ref.get("preview")
        refs_changed = False
        if fields is None or cached_preview is None:
            format_name = raw_ref.get("format", "jsonl")
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=f".{format_name}"
            ) as tmp:
                inspection_path = Path(tmp.name)
            self._artifact_store.materialize(raw_ref["uri"], inspection_path)
            try:
                reader = source_reader(inspection_path, format_name)
                description = reader.describe()
                fields = description.fields
                cached_preview = reader.preview(max(preview_limit, 5))
            finally:
                inspection_path.unlink(missing_ok=True)
            raw_ref["fields"] = fields
            raw_ref["preview"] = cached_preview
            raw_ref.setdefault("record_count", description.record_count)
            raw_ref.setdefault("media_type", description.media_type)
            refs_changed = True
        preview = [
            {
                name: self._preview_value(value, max_text_length)
                for name, value in record.items()
            }
            for record in cached_preview[:preview_limit]
        ]
        recommendation = refs.get("mapping_recommendation")
        if recommendation is None:
            resolved = self._mapping_resolver.resolve(
                [field["name"] for field in fields]
            )
            if resolved is not None:
                recommendation = {
                    "mapping": resolved.mapping.model_dump(mode="json"),
                    "provenance": resolved.provenance(),
                    "adapter_parameters": resolved.adapter_parameters,
                }
                refs["mapping_recommendation"] = recommendation
                refs_changed = True
        if refs_changed and version.status == DatasetVersionStatus.DRAFT:
            refs["raw_file"] = raw_ref
            self._version_repository.update_artifact_refs(version_id, refs)
        return {
            "source_type": f"{raw_ref.get('format', 'jsonl')}_upload",
            "filename": raw_ref.get("filename", Path(raw_ref["uri"]).name),
            "size_bytes": raw_ref.get("size_bytes"),
            "format": raw_ref.get("format", "jsonl"),
            "record_count": raw_ref.get("record_count"),
            "fields": fields,
            "rows": preview,
            "mapping_recommendation": recommendation,
            "mapping_match": self._mapping_resolver.explain(
                [field["name"] for field in fields]
            ),
        }

    @staticmethod
    def _parse_jsonl(content: bytes) -> list[dict[str, Any]]:
        text = content.decode("utf-8-sig")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    @staticmethod
    def _json_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        return "object"

    @staticmethod
    def _preview_value(value: Any, max_text_length: int) -> Any:
        if isinstance(value, str) and len(value) > max_text_length:
            return value[:max_text_length]
        return value

    def get_version(self, version_id: UUID) -> DatasetVersion | None:
        return self._version_repository.get_by_id(version_id)

    def get_setup_status(self, version_id: UUID) -> dict[str, bool]:
        refs = self._version_repository.get_artifact_refs(version_id) or {}
        return {
            "has_raw_file": "raw_file" in refs,
            "has_field_mapping": "field_mapping" in refs,
        }

    @staticmethod
    def _validate_jsonl(content: bytes) -> None:
        """Fail during upload instead of deferring malformed input to publish."""
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("JSONL file must be UTF-8 encoded") from exc

        record_count = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            record_count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise TypeError(f"Line {line_number} must be a JSON object")
        if record_count == 0:
            raise ValueError("JSONL file contains no records")

    @staticmethod
    def _format_from_filename(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix == ".jsonl":
            return "jsonl"
        if suffix == ".parquet":
            return "parquet"
        raise ValueError("Only .jsonl and .parquet files are supported")

    def publish_version(
        self,
        version_id: UUID,
    ) -> dict[str, Any]:
        """发布版本：运行标准化、生成 Manifest/Profile，状态变为 PUBLISHED。"""
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        if version.status != DatasetVersionStatus.DRAFT:
            raise ValueError("Only DRAFT versions can be published")

        refs = self._version_repository.get_artifact_refs(version_id)
        if not refs or "raw_file" not in refs:
            raise ValueError("No raw file uploaded for this version")
        if "field_mapping" not in refs:
            raise ValueError("No FieldMappingSpec configured for this version")
        field_mapping = FieldMappingSpecV2.model_validate(refs["field_mapping"])

        raw_uri = refs["raw_file"]["uri"]
        input_format = refs["raw_file"].get("format", "jsonl")
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=f".{input_format}"
        ) as tmp:
            raw_path = Path(tmp.name)
        self._artifact_store.materialize(raw_uri, raw_path)

        try:
            result = self._normalizer.normalize(
                raw_path,
                dataset_id=version.dataset_id,
                dataset_version_id=version_id,
                field_mapping=field_mapping,
                input_format=input_format,
                mapping_provenance=refs.get("mapping_provenance"),
                adapter_parameters=(refs.get("mapping_recommendation") or {}).get(
                    "adapter_parameters", {}
                ),
            )
        finally:
            raw_path.unlink(missing_ok=True)

        # Update version status and artifact refs
        now = datetime.now(UTC)
        version.mark_registered()
        version.publish(now)
        self._version_repository.update(version)

        artifact_refs = {
            "raw_file": refs["raw_file"],
            "field_mapping": refs["field_mapping"],
            "mapping_provenance": refs.get("mapping_provenance"),
            "mapping_recommendation": refs.get("mapping_recommendation"),
            "manifest": {
                "uri": f"datasets/{version.dataset_id}/{version_id}/dataset_manifest.json",
            },
            "normalized": {
                "uri": result.normalized_artifact.uri,
                "sha256": result.normalized_artifact.checksum.value,
            },
            "rejected": {
                "uri": result.rejected_artifact.uri,
                "sha256": result.rejected_artifact.checksum.value,
            },
        }
        self._version_repository.update_artifact_refs(
            version_id,
            artifact_refs,
            content_fingerprint=refs["raw_file"].get("sha256"),
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as tmp:
            normalized_path = Path(tmp.name)
        self._artifact_store.materialize(
            result.normalized_artifact.uri, normalized_path
        )
        try:
            with normalized_path.open("rb") as normalized_source:
                records = (
                    SampleRecordV2.model_validate_json(line)
                    for line in normalized_source
                    if line.strip()
                )
                profile = self._profiler.profile(
                    result.manifest,
                    records,
                    normalized_size_bytes=result.normalized_artifact.size_bytes,
                )
        finally:
            normalized_path.unlink(missing_ok=True)
        profile_checksum = hashlib.sha256(
            json.dumps(
                profile.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        self._profile_repository.add(profile, profile_checksum)

        return {
            "version_id": str(version_id),
            "status": version.status.value,
            "published_at": version.published_at.isoformat()
            if version.published_at
            else None,
            "manifest": result.manifest.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "normalization_run": result.normalization_run.model_dump(mode="json"),
            "counts": {
                "input": result.normalization_run.counts.input,
                "normalized": result.normalization_run.counts.normalized,
                "rejected": result.normalization_run.counts.rejected,
            },
        }

    def get_manifest(self, version_id: UUID) -> DatasetManifestV2:
        """读取已发布版本的 DatasetManifest 2.0。"""
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")

        manifest_relative = (
            f"datasets/{version.dataset_id}/{version_id}/dataset_manifest.json"
        )
        content = self._artifact_store.read(manifest_relative)
        return DatasetManifestV2.model_validate_json(content)

    def get_profile(self, version_id: UUID) -> DatasetProfileV1:
        """读取已发布版本的 DatasetProfile 1.0。"""
        version = self._version_repository.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")

        profile = self._profile_repository.get_by_dataset_version(version_id)
        if profile is None:
            raise ValueError("Dataset profile has not been generated")
        return profile

    def list_versions(self, dataset_id: UUID) -> list[DatasetVersion]:
        return self._version_repository.list_by_dataset(dataset_id)

    def delete_version(self, dataset_id: UUID, version_id: UUID) -> bool:
        """删除指定数据集下的版本（含 manifest / profile 附属数据）。

        - 版本不存在：返回 False（路由层映射 404）
        - 版本不属于该数据集：抛出 ValueError（路由层映射 404）
        - 存在下游评测依赖：抛出 DatasetVersionInUseError（路由层映射 409）
        """

        version = self._version_repository.get_by_id(version_id)

        if version is None:
            return False

        if version.dataset_id != dataset_id:
            raise ValueError(
                f"DatasetVersion {version_id} does not belong to dataset {dataset_id}"
            )

        dependencies = self._version_repository.count_blocking_dependencies(version_id)
        if any(dependencies.values()):
            raise DatasetVersionInUseError(dependencies)

        try:
            self._version_repository.delete(version_id)
        except IntegrityError as exc:
            # 依赖检查与删除之间存在并发新增引用的窗口，由 RESTRICT 兜底
            logger.warning(
                "Concurrent reference detected while deleting version %s: %s",
                version_id,
                exc,
            )
            raise DatasetVersionInUseError(
                {}, message="版本删除时检测到并发引用，请重试"
            ) from exc

        return True

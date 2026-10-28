# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 闭环 2：Dataset Version 上传、发布、Manifest、Sample 预览与 Profile API。
# ====================================================

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from apps.api.dependencies import (
    get_artifact_store,
    get_dataset_version_service,
    get_quality_result_service,
)
from qa_eval.application.services.dataset_version_service import (
    DatasetVersionService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.domain.ports.artifact_store import ArtifactStore

router = APIRouter(prefix="/api/v1", tags=["dataset-versions"])


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=64)


class VersionResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    version: str
    status: str
    created_at: str | None
    published_at: str | None
    has_raw_file: bool
    has_field_mapping: bool


def _version_response(version, service: DatasetVersionService) -> VersionResponse:
    setup = service.get_setup_status(version.id)
    return VersionResponse(
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        status=version.status.value,
        created_at=version.created_at.isoformat() if version.created_at else None,
        published_at=version.published_at.isoformat() if version.published_at else None,
        **setup,
    )


@router.post(
    "/datasets/{dataset_id}/versions", response_model=VersionResponse, status_code=201
)
def create_dataset_version(
    dataset_id: UUID,
    payload: VersionCreate,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        version = service.create_version(dataset_id, payload.version_label)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _version_response(version, service)


@router.get("/datasets/{dataset_id}/versions", response_model=list[VersionResponse])
def list_dataset_versions(
    dataset_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    versions = service.list_versions(dataset_id)
    return [
        _version_response(version, service)
        for version in versions
    ]


@router.delete(
    "/datasets/{dataset_id}/versions/{version_id}", status_code=204
)
def delete_dataset_version(
    dataset_id: UUID,
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        deleted = service.delete_version(dataset_id, version_id)
    except DatasetVersionInUseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "dependencies": exc.dependencies,
            },
        ) from exc
    except ValueError as exc:
        # 版本存在但不属于指定数据集，与不存在同样返回 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="DatasetVersion not found",
        )


@router.post("/dataset-versions/{version_id}/files")
def upload_raw_file(
    version_id: UUID,
    file: Annotated[UploadFile, File()],
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    if file.filename is None or not file.filename.endswith(".jsonl"):
        raise HTTPException(status_code=422, detail="Only .jsonl files are supported")

    content = file.file.read()
    try:
        result = service.upload_raw_file(version_id, file.filename, content)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result


@router.put(
    "/dataset-versions/{version_id}/field-mapping",
    response_model=FieldMappingSpecV2,
)
def save_field_mapping(
    version_id: UUID,
    mapping: FieldMappingSpecV2,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        return service.save_field_mapping(version_id, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/dataset-versions/{version_id}/field-mapping",
    response_model=FieldMappingSpecV2 | None,
)
def get_field_mapping(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    return service.get_field_mapping(version_id)


@router.get("/dataset-versions/{version_id}/source-inspection")
def inspect_uploaded_source(
    version_id: UUID,
    limit: int = 5,
    max_text_length: int = 300,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if max_text_length < 1 or max_text_length > 5000:
        raise HTTPException(
            status_code=422,
            detail="max_text_length must be between 1 and 5000",
        )
    try:
        return service.inspect_uploaded_source(
            version_id,
            preview_limit=limit,
            max_text_length=max_text_length,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/dataset-versions/{version_id}/publish")
def publish_dataset_version(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        result = service.publish_version(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result


@router.get("/dataset-versions/{version_id}")
def get_dataset_version(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    version = service.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="DatasetVersion not found")

    return _version_response(version, service)


@router.get("/dataset-versions/{version_id}/manifest")
def get_dataset_version_manifest(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        manifest = service.get_manifest(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return manifest.model_dump(mode="json")


@router.get("/dataset-versions/{version_id}/samples")
def get_dataset_version_samples(
    version_id: UUID,
    limit: int = 5,
    offset: int = 0,
    service: DatasetVersionService = Depends(get_dataset_version_service),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
):
    try:
        manifest = service.get_manifest(version_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    normalized_artifact = next(
        (a for a in manifest.artifacts if a.kind == "NORMALIZED_DATA"),
        None,
    )
    if normalized_artifact is None:
        raise HTTPException(status_code=404, detail="Normalized artifact not found")

    content = artifact_store.read(normalized_artifact.uri)
    lines = content.decode("utf-8").strip().split("\n")
    total = len(lines)
    page = lines[offset : offset + limit]
    samples = [json.loads(line) for line in page if line.strip()]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "samples": samples,
    }


@router.get("/dataset-versions/{version_id}/samples/{sample_id}")
def get_dataset_version_sample_by_id(
    version_id: UUID,
    sample_id: str,
    quality_service: QualityResultService = Depends(get_quality_result_service),
):
    try:
        sample = quality_service.get_sample_by_id(version_id, sample_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if sample is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    return sample.model_dump(mode="json")


@router.get("/dataset-versions/{version_id}/profile")
def get_dataset_version_profile(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        profile = service.get_profile(version_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return profile.model_dump(mode="json")

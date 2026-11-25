# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:30:58
# @LastEditTime: 2026-10-19 16:32:52
# @Description: Dataset API
# ====================================================

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_dataset_service, get_db_session
from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.application.services.dataset_version_service import DatasetVersionService
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.domain.entities.dataset import Dataset
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)

router = APIRouter(
    prefix="/api/v1/datasets",
    tags=["datasets"],
)


DataType = Literal["structured", "semi_structured", "unstructured"]
SourceKind = Literal["file", "database", "unconfigured"]


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    modality: list[str] = Field(min_length=1)
    description: str | None = None
    data_type: DataType | None = None
    owner: str | None = Field(default=None, max_length=255)


class DatasetUpdate(DatasetCreate):
    enabled: bool


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    modality: list[str]
    description: str | None
    created_at: datetime | None
    updated_at: datetime | None
    data_type: str | None = None
    owner: str | None = None
    enabled: bool = True
    source_kind: str | None = None
    storage_path: str | None = None


class DatasetPage(BaseModel):
    items: list[DatasetResponse]
    total: int


def to_response(dataset: Dataset) -> DatasetResponse:
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        modality=dataset.modality,
        description=dataset.description,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        data_type=dataset.data_type,
        owner=dataset.owner,
        enabled=dataset.enabled,
        source_kind=dataset.source_kind,
        storage_path=dataset.storage_path,
    )


@router.post("", response_model=DatasetResponse, status_code=201)
def register_dataset(
    payload: DatasetCreate,
    service: DatasetService = Depends(get_dataset_service),
):
    try:
        dataset = service.register_dataset(
            name=payload.name,
            modality=payload.modality,
            description=payload.description,
            **payload.model_dump(include={"data_type", "owner"}, exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return to_response(dataset)


@router.get("", response_model=list[DatasetResponse])
def list_datasets(
    service: DatasetService = Depends(get_dataset_service),
):
    return [
        to_response(dataset)
        for dataset in service.list_datasets()
    ]


@router.get("/search", response_model=DatasetPage)
def search_datasets(
    session: Session = Depends(get_db_session),
    q: str | None = Query(default=None, max_length=255),
    data_type: DataType | None = None,
    modality: str | None = Query(default=None, max_length=64),
    source_kind: SourceKind | None = None,
    enabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    rows, total = SQLAlchemyDatasetRepository(session).search(
        q=q, data_type=data_type, modality=modality, source_kind=source_kind,
        enabled=enabled, page=page, size=size,
    )
    return DatasetPage(items=[to_response(row) for row in rows], total=total)


@router.put("/{dataset_id}", response_model=DatasetResponse)
def update_dataset(
    dataset_id: UUID, payload: DatasetUpdate, session: Session = Depends(get_db_session),
):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="数据集名称不能为空")
    updated = SQLAlchemyDatasetRepository(session).update_metadata(dataset_id, payload.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return to_response(updated)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: UUID,
    service: DatasetService = Depends(get_dataset_service),
):
    dataset = service.get_dataset(dataset_id)

    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset not found",
        )

    return to_response(dataset)


class DefaultMappingUpdate(BaseModel):
    reference_version_id: UUID
    mapping: FieldMappingSpecV2


@router.get("/{dataset_id}/source-config")
def get_source_config(dataset_id: UUID, session: Session = Depends(get_db_session)):
    dataset = session.get(DatasetModel, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    config = dataset.source_config or {}
    return {key: config.get(key) for key in ("storage", "fields", "mapping", "reference_version_id", "table", "engine")}


@router.put("/{dataset_id}/source-config")
def update_source_config(
    dataset_id: UUID, payload: DefaultMappingUpdate, session: Session = Depends(get_db_session),
):
    dataset = session.get(DatasetModel, dataset_id, with_for_update=True)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if dataset.source_kind not in ("file", "database") or not dataset.source_config:
        raise HTTPException(status_code=409, detail="请先发布一个版本以建立数据源配置")
    version = session.get(DatasetVersionModel, payload.reference_version_id)
    if version is None or version.dataset_id != dataset_id or version.status != "PUBLISHED":
        raise HTTPException(status_code=422, detail="请选择该数据集的已发布参考版本")
    raw = (version.artifact_refs or {}).get("raw_file", {})
    fields = (raw.get("source_snapshot") or {}).get("fields", raw.get("fields"))
    if not fields:
        raise HTTPException(status_code=422, detail="参考版本缺少已探测的 Schema")
    try:
        DatasetVersionService.validate_field_mapping(payload.mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dataset.source_config = {
        **dataset.source_config,
        "fields": fields,
        "mapping": payload.mapping.model_dump(mode="json"),
        "reference_version_id": str(version.id),
    }
    session.flush()
    return {key: dataset.source_config.get(key) for key in ("storage", "fields", "mapping", "reference_version_id", "table", "engine")}


@router.get("/{dataset_id}/deletion-preview")
def get_dataset_deletion_preview(
    dataset_id: UUID,
    service: DatasetService = Depends(get_dataset_service),
):
    preview = service.get_deletion_preview(dataset_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return preview


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: UUID,
    cascade: bool = Query(default=False),
    service: DatasetService = Depends(get_dataset_service),
):
    try:
        deleted = (
            service.delete_dataset(dataset_id, cascade=True)
            if cascade
            else service.delete_dataset(dataset_id)
        )
    except DatasetVersionInUseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "dependencies": exc.dependencies,
            },
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Dataset not found",
        )



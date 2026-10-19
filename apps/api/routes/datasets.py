# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:30:58
# @LastEditTime: 2026-10-19 16:32:52
# @Description: Dataset API
# ====================================================

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.dependencies import get_dataset_service
from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.domain.entities.dataset import Dataset

router = APIRouter(
    prefix="/api/v1/datasets",
    tags=["datasets"],
)


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    modality: list[str] = Field(min_length=1)
    description: str | None = None


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    modality: list[str]
    description: str | None
    created_at: datetime | None
    updated_at: datetime | None


def to_response(dataset: Dataset) -> DatasetResponse:
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        modality=dataset.modality,
        description=dataset.description,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
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

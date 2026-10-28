from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import get_quality_contract_service
from qa_eval.application.services.quality_contract_service import (
    PublishedQualityContract,
    QualityContractMultiPublishRequest,
    QualityContractPublishRequest,
    QualityContractService,
)

router = APIRouter(
    prefix="/api/v1/quality-contract-versions", tags=["quality-contracts"]
)
Service = Annotated[QualityContractService, Depends(get_quality_contract_service)]


@router.post("", response_model=PublishedQualityContract, status_code=201)
def publish_contract(payload: QualityContractPublishRequest, service: Service):
    try:
        return service.publish(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/multi", response_model=PublishedQualityContract, status_code=201)
def publish_multi_contract(
    payload: QualityContractMultiPublishRequest, service: Service
):
    try:
        return service.publish_multi(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[PublishedQualityContract])
def list_contracts(service: Service):
    return service.list_published()


@router.get("/{contract_version_id}", response_model=PublishedQualityContract)
def get_contract(contract_version_id: UUID, service: Service):
    result = service.get(contract_version_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Quality Contract Version not found"
        )
    return result

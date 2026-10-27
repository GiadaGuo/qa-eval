from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import get_metric_registry_service
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

router = APIRouter(prefix="/api/v1", tags=["Metric Registry"])
Service = Annotated[MetricRegistryService, Depends(get_metric_registry_service)]


@router.get("/metrics", response_model=list[MetricDefinitionV1])
def metrics(service: Service):
    service.seed()
    return service.list_metrics()


@router.get("/metrics/{key}/versions/{version}", response_model=MetricDefinitionV1)
def metric(key: str, version: str, service: Service):
    service.seed()
    result = service.get_metric(key, version)
    if result is None:
        raise HTTPException(404, "Metric definition not found")
    return result


@router.get("/calculators", response_model=list[CalculatorDescriptorV1])
def calculators(service: Service):
    service.seed()
    return service.list_calculators()


@router.get(
    "/calculators/{key}/versions/{version}", response_model=CalculatorDescriptorV1
)
def calculator(key: str, version: str, service: Service):
    service.seed()
    result = service.get_calculator(key, version)
    if result is None:
        raise HTTPException(404, "Calculator not found")
    return result


@router.get(
    "/metric-calculator-bindings", response_model=list[MetricCalculatorBindingV1]
)
def bindings(service: Service, metric_key: str | None = None):
    service.seed()
    return service.list_bindings(metric_key)

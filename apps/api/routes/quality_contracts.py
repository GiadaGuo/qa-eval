from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.dependencies import (
    get_evaluation_run_service,
    get_quality_contract_service,
)
from qa_eval.application.normalization.consistency_input_slots import consistency_input_contract
from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.application.services.document_evaluation_target import (
    DocumentContractRequest,
)
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    PublishedQualityContract,
    QualityContractMultiPublishRequest,
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.calculators.protocol import CalculatorError
from qa_eval.contracts.logical_inputs import InputSelector
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel

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


@router.post("/document", response_model=PublishedQualityContract, status_code=201)
def publish_document_contract(payload: DocumentContractRequest, service: Service):
    try:
        return service.publish_document(payload)
    except (ValueError, CalculatorError) as exc:
        raise HTTPException(422, str(exc)) from exc


Runner = Annotated[EvaluationRunService, Depends(get_evaluation_run_service)]


@router.post("/multi", response_model=PublishedQualityContract, status_code=201)
def publish_multi_contract(payload: QualityContractMultiPublishRequest, service: Service, runner: Runner):
    return prepare_multi_contract(payload, service, runner)


@router.post("/preview", response_model=PublishedQualityContract)
def preview_multi_contract(payload: QualityContractMultiPublishRequest, service: Service, runner: Runner):
    return prepare_multi_contract(payload, service, runner, preview=True)


def prepare_multi_contract(
    payload: QualityContractMultiPublishRequest,
    service: QualityContractService,
    runner: EvaluationRunService,
    *,
    preview: bool = False,
) -> PublishedQualityContract:
    try:
        if payload.excluded_metrics is not None:
            version = runner.session.get(DatasetVersionModel, payload.dataset_version_id)
            for item in [*payload.items, *payload.observation_items]:
                if not item.calculator_key or not item.calculator_version:
                    raise ValueError("人工配置需指定算子及版本")
                request = ContractInputPreflightRequest(
                    dataset_version_id=payload.dataset_version_id,
                    metric_key=item.metric_key, metric_version=item.metric_version,
                    calculator_key=item.calculator_key, calculator_version=item.calculator_version,
                    parameters=item.parameters,
                    mapping_digest=getattr(item, "mapping_digest", None) or
                    (version.default_mapping_digest if version else None),
                    input_selectors=getattr(item, "input_selectors", {}),
                )
                result = preflight_contract_inputs(request, runner)
                if result["status"] != "READY":
                    raise ValueError(f'{item.key}: {result["status"]} · {result.get("detail", "")}')
                if result.get("plan") and request.mapping_digest and hasattr(item, "mapping_digest"):
                    item.mapping_digest = request.mapping_digest
        return service.publish_multi(payload, preview=preview)
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


class ContractInputPreflightRequest(BaseModel):
    dataset_version_id: UUID
    metric_key: str
    metric_version: str
    calculator_key: str
    calculator_version: str
    mapping_digest: str | None = None
    input_selectors: dict[str, InputSelector] = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)


@router.post("/input-preflight")
def preflight_contract_inputs(
    payload: ContractInputPreflightRequest,
    runner: Annotated[EvaluationRunService, Depends(get_evaluation_run_service)],
):
    registry = MetricRegistryService(runner.session)
    version = runner.session.get(DatasetVersionModel, payload.dataset_version_id)
    calculator = registry.get_calculator(payload.calculator_key, payload.calculator_version)
    metric = registry.get_metric(payload.metric_key, payload.metric_version)
    binding = next((item for item in registry.list_bindings(payload.metric_key)
                    if item.metric_version == payload.metric_version
                    and item.calculator_key == payload.calculator_key
                    and item.calculator_version == payload.calculator_version), None)
    if version is None or version.status != "PUBLISHED":
        return {"status": "CONTEXT_MISSING", "detail": "Published DatasetVersion required"}
    if metric is None or calculator is None or binding is None:
        return {"status": "CONTEXT_MISSING", "detail": "Registry binding not found"}
    if not binding.enabled or calculator.availability.status != "AVAILABLE":
        return {"status": "DISABLED", "detail": "Calculator or binding is disabled"}
    dataset = runner.session.get(DatasetModel, version.dataset_id)
    context, _ = ApplicabilityService.context_for(runner.session, version, dataset)
    resolved = runner._resolved_parameters(calculator, payload.parameters)
    raw = (version.artifact_refs or {}).get("raw_record_manifest")
    logical = calculator.input_contract.logical
    slots = [slot.model_dump(mode="json") for slot in logical.slots] if logical else []
    try:
        runner._validate_parameter_schema(calculator, resolved)
        logical = consistency_input_contract(calculator, resolved)
        slots = [slot.model_dump(mode="json") for slot in logical.slots] if logical else []
        if raw and logical is None:
            raise ValueError("CONTRACT_NOT_MIGRATED: Direct v3 Input Contract required")
        decision = ApplicabilityService().evaluate(context, metric, calculator, binding, resolved)
        if decision.status != "APPLICABLE":
            findings = [item.code for item in decision.findings]
            code = ("ARTIFACT_MISSING" if "MISSING_INPUT_ARTIFACT" in findings else
                    "FIELD_NOT_SELECTED" if "MISSING_SELECTOR_PARAMETER" in findings else
                    "NOT_APPLICABLE" if decision.status == "NOT_APPLICABLE" else "CONTEXT_MISSING")
            return {"status": code, "detail": ", ".join(findings), "slots": slots}
        if raw:
            plan = runner.preflight_inputs(
                version, calculator, mapping_digest=payload.mapping_digest, selectors=payload.input_selectors,
                parameters=resolved,
            )
            return {"status": "READY", "slots": slots, "plan": plan.model_dump(mode="json")}
        if logical:
            raise ValueError("ARTIFACT_MISSING: v3 Manifest required")
    except ValueError as exc:
        code, _, detail = str(exc).partition(": ")
        return {"status": code if code.isupper() and " " not in code else "INVALID_CONFIGURATION",
                "detail": detail or str(exc), "slots": slots}
    return {"status": "READY", "slots": slots}

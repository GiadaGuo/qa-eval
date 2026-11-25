import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile

from apps.api.dependencies import get_metric_registry_service
from qa_eval.application.services.metric_registry_service import (
    InvalidRegistryDescriptorError,
    MetricRegistryService,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorError,
    CalculatorOutput,
    CalculatorValidationError,
)
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

router = APIRouter(prefix="/api/v1", tags=["Metric Registry"])
Service = Annotated[MetricRegistryService, Depends(get_metric_registry_service)]


@router.get("/metrics", response_model=list[MetricDefinitionV1])
def metrics(service: Service):
    return service.list_metrics()


@router.get("/metrics/{key}/versions/{version}", response_model=MetricDefinitionV1)
def metric(key: str, version: str, service: Service):
    result = service.get_metric(key, version)
    if result is None:
        raise HTTPException(404, "Metric definition not found")
    return result


@router.get("/calculators", response_model=list[CalculatorDescriptorV1])
def calculators(service: Service, response: Response):
    validation = service.calculator_validation()
    response.headers["X-Registry-Invalid-Calculators"] = str(len(validation["invalid"]))
    return service.list_calculators()


@router.get(
    "/calculators/{key}/versions/{version}", response_model=CalculatorDescriptorV1
)
def calculator(key: str, version: str, service: Service):
    try:
        result = service.get_calculator(key, version)
    except InvalidRegistryDescriptorError as exc:
        raise HTTPException(
            422,
            {
                "code": "INVALID_REGISTRY_DESCRIPTOR",
                **exc.details,
            },
        ) from exc
    if result is None:
        raise HTTPException(404, "Calculator not found")
    return result


@router.get(
    "/metric-calculator-bindings", response_model=list[MetricCalculatorBindingV1]
)
def bindings(service: Service, metric_key: str | None = None):
    return service.list_bindings(metric_key)


@router.post("/registry/sync")
def sync_registry(service: Service):
    return service.sync()


@router.get("/registry/readiness")
def registry_readiness(service: Service):
    return service.runtime_readiness()


@router.get("/registry/validation")
def registry_validation(service: Service):
    return service.calculator_validation()


@router.post("/calculators/{key}/versions/{version}/test-document")
def test_document_calculator(
    key: str,
    version: str,
    service: Service,
    document: UploadFile,
    parameters: Annotated[str, Form()] = "{}",
):
    descriptor = calculator(key, version, service)
    logical = descriptor.input_contract.logical
    if logical is None or logical.grain != "DOCUMENT":
        raise HTTPException(422, "该算子不接受单文档测试")
    bindings = [
        binding
        for binding in service.list_bindings()
        if binding.enabled
        and (binding.calculator_key, binding.calculator_version) == (key, version)
    ]
    if len(bindings) != 1:
        raise HTTPException(422, "单文档算子须有一个启用的指标绑定")
    binding = bindings[0]
    try:
        args = json.loads(parameters)
    except ValueError as exc:
        raise HTTPException(422, "参数须为 JSON 对象") from exc
    if not isinstance(args, dict):
        raise HTTPException(422, "参数须为 JSON 对象")
    suffix = Path(document.filename or "").suffix.lower()
    if suffix not in {".md", ".pdf", ".docx"}:
        raise HTTPException(422, "说明文档须为 MD、PDF 或 DOCX")
    limit = 8 * 1024 * 1024
    content = document.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, "说明文档超过 8 MiB")
    with TemporaryDirectory(prefix="qa-eval-document-") as tmp:
        root = Path(tmp)
        source = root / f"input{suffix}"
        source.write_bytes(content)
        context = CalculatorContext(
            run_id=uuid4(),
            stage_run_id=uuid4(),
            dataset_version_id=None,
            metric_key=binding.metric_key,
            metric_version=binding.metric_version,
            input_path=source,
            output_dir=root,
            input_schema_ref="DOCUMENT",
            input_artifacts={"DOCUMENT": source},
        )
        try:
            implementation = TrustedCalculatorLoader().load(descriptor)
            result = implementation.execute(context, args)
            if not isinstance(result, CalculatorOutput):
                raise CalculatorError("单文档算子必须返回指标观察值")
        except CalculatorValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        except CalculatorError as exc:
            raise HTTPException(502, str(exc)) from exc

        return {
            "metric_key": binding.metric_key,
            "metric_version": binding.metric_version,
            "calculator_key": key,
            "calculator_version": version,
            "status": result.status,
            "status_detail": result.status_detail,
            "measurements": result.observations,
            "population": result.population,
            "semantic_coverage": result.semantic_coverage,
            "evidence": [
                {
                    "kind": item.kind,
                    "schema_ref": item.schema_ref,
                    "subject_scope": item.subject_scope,
                    "contains_raw_content": item.contains_raw_content,
                    "redaction_policy": item.redaction_policy,
                    "record_count": item.record_count,
                    "records": [
                        json.loads(line)
                        for line in item.path.read_text(encoding="utf-8").splitlines()
                    ],
                }
                for item in result.evidence
            ],
        }

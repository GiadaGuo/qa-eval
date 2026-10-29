from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.registry import (
    CalculatorImplementationModel,
    MetricCalculatorBindingModel,
    MetricDefinitionModel,
)
from qa_eval.infrastructure.uuid7 import uuid7


class ContractRuleRequest(BaseModel):
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NE"]
    threshold: Any
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    on_missing: Literal["UNKNOWN", "SKIPPED"]


class QualityContractPublishRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=64)
    dataset_version_id: UUID
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    measurement: str = Field(min_length=1)
    calculator_key: str = Field(min_length=1)
    calculator_version: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rule: ContractRuleRequest


class QualityContractItemPublishRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=255)
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    measurement: str = Field(min_length=1)
    calculator_key: str = Field(min_length=1)
    calculator_version: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rule: ContractRuleRequest


class ContractGateRequest(BaseModel):
    unknown_policy: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    fail_on_severities: list[Literal["INFO", "WARNING", "ERROR", "CRITICAL"]] = Field(
        default_factory=lambda: ["WARNING", "ERROR", "CRITICAL"], min_length=1
    )


class QualityContractMultiPublishRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=64)
    dataset_version_id: UUID
    items: list[QualityContractItemPublishRequest] = Field(min_length=1)
    gate: ContractGateRequest = Field(default_factory=ContractGateRequest)

    @model_validator(mode="after")
    def item_keys_are_unique(self) -> QualityContractMultiPublishRequest:
        keys = [
            item.key or f"{item.metric_key}_{item.measurement}" for item in self.items
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("contract item keys must be unique")
        return self


class PublishedQualityContract(BaseModel):
    target_dataset_version_id: UUID
    contract: QualityContractVersionV1


class QualityContractService:
    def __init__(self, session: Session):
        self.session = session

    def publish(
        self, request: QualityContractPublishRequest
    ) -> PublishedQualityContract:
        return self.publish_multi(
            QualityContractMultiPublishRequest(
                name=request.name,
                version_label=request.version_label,
                dataset_version_id=request.dataset_version_id,
                items=[
                    QualityContractItemPublishRequest(
                        metric_key=request.metric_key,
                        metric_version=request.metric_version,
                        measurement=request.measurement,
                        calculator_key=request.calculator_key,
                        calculator_version=request.calculator_version,
                        parameters=request.parameters,
                        rule=request.rule,
                    )
                ],
            )
        )

    def publish_multi(
        self, request: QualityContractMultiPublishRequest
    ) -> PublishedQualityContract:
        dataset_version = self.session.get(
            DatasetVersionModel, request.dataset_version_id
        )
        if dataset_version is None:
            raise ValueError("DatasetVersion not found")
        if dataset_version.status != "PUBLISHED":
            raise ValueError("Quality Contract requires a PUBLISHED DatasetVersion")
        if (
            not dataset_version.artifact_refs
            or "manifest" not in dataset_version.artifact_refs
        ):
            raise ValueError("Published DatasetVersion has no manifest artifact")

        dataset = self.session.get(DatasetModel, dataset_version.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found for selected version")

        context, _ = ApplicabilityService.context_for(
            self.session, dataset_version, dataset
        )
        validated = [self.validate_item(context, item) for item in request.items]

        now = datetime.now(UTC)
        contract_id = uuid7()
        contract_version_id = uuid7()
        body = {
            "schema_version": "1.0",
            "contract_id": contract_id,
            "contract_version_id": contract_version_id,
            "version_label": request.version_label,
            "name": request.name,
            "status": "PUBLISHED",
            "applicability": {
                "modalities": sorted(
                    {
                        modality
                        for metric, _, _, _, _ in validated
                        for modality in metric.applicability.modalities
                    }
                ),
                "task_types": [],
            },
            "items": [
                {
                    "key": item.key or f"{item.metric_key}_{item.measurement}",
                    "metric": {
                        "key": item.metric_key,
                        "version": item.metric_version,
                        "measurement": item.measurement,
                    },
                    "scope": metric.supported_scopes[0],
                    "calculation": {
                        "parameters": item.parameters,
                        "implementation_constraints": {
                            "deterministic": calculator.deterministic,
                            "attributes": {
                                "calculator_key": item.calculator_key,
                                "calculator_version": item.calculator_version,
                                "binding_priority": binding.priority,
                            },
                        },
                    },
                    "rule": {
                        "operator": item.rule.operator,
                        "threshold": item.rule.threshold,
                    },
                    "severity": item.rule.severity,
                    "on_missing": item.rule.on_missing,
                }
                for item, (metric, calculator, binding, _, _) in zip(
                    request.items, validated, strict=True
                )
            ],
            "gate": {
                "unknown_policy": request.gate.unknown_policy,
                "fail_on_severities": request.gate.fail_on_severities,
            },
            "published_at": now,
        }
        canonical = json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))
        body["fingerprint"] = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        contract = QualityContractVersionV1.model_validate(body)
        self.session.add(
            QualityContractVersionModel(
                contract_version_id=contract.contract_version_id,
                contract_id=contract.contract_id,
                target_dataset_version_id=request.dataset_version_id,
                version_label=contract.version_label,
                name=contract.name,
                status=contract.status,
                fingerprint=contract.fingerprint,
                document=contract.model_dump(mode="json"),
                published_at=now,
            )
        )
        self.session.flush()
        return PublishedQualityContract(
            target_dataset_version_id=request.dataset_version_id,
            contract=contract,
        )

    def validate_item(self, context, item: QualityContractItemPublishRequest):
        metric = self._metric(item.metric_key, item.metric_version)
        calculator = self._calculator(item.calculator_key, item.calculator_version)
        binding = self._binding_values(
            item.metric_key,
            item.metric_version,
            item.calculator_key,
            item.calculator_version,
        )
        measurement = next(
            (value for value in metric.measurements if value.key == item.measurement),
            None,
        )
        if measurement is None:
            raise ValueError("Unknown measurement for selected metric")
        self._validate_parameters(item.parameters, calculator.parameter_schema)
        self._validate_threshold(item.rule.threshold, measurement.value_type)
        applicability = ApplicabilityService().evaluate(
            context, metric, calculator, binding, item.parameters
        )
        runtime_decidable = (
            calculator.execution.mode == "SHARED_INFERENCE"
            and applicability.status in {"NEEDS_INPUT", "NOT_APPLICABLE"}
        )
        if applicability.status != "APPLICABLE" and not runtime_decidable:
            codes = ", ".join(value.code for value in applicability.findings)
            raise ValueError(f"Metric/Calculator is {applicability.status}: {codes}")
        return metric, calculator, binding, measurement, applicability

    def list_published(self) -> list[PublishedQualityContract]:
        rows = self.session.scalars(
            select(QualityContractVersionModel).order_by(
                QualityContractVersionModel.published_at.desc()
            )
        ).all()
        return [self._to_contract(row) for row in rows]

    def get(self, contract_version_id: UUID) -> PublishedQualityContract | None:
        row = self.session.get(QualityContractVersionModel, contract_version_id)
        return None if row is None else self._to_contract(row)

    @staticmethod
    def _to_contract(row: QualityContractVersionModel) -> PublishedQualityContract:
        return PublishedQualityContract(
            target_dataset_version_id=row.target_dataset_version_id,
            contract=QualityContractVersionV1.model_validate(row.document),
        )

    def _metric(self, key: str, version: str) -> MetricDefinitionV1:
        row = self.session.scalar(
            select(MetricDefinitionModel).filter_by(metric_key=key, version=version)
        )
        if row is None:
            raise ValueError("Metric definition not found")
        return MetricDefinitionV1.model_validate(row.document)

    def _calculator(self, key: str, version: str) -> CalculatorDescriptorV1:
        row = self.session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key=key, version=version
            )
        )
        if row is None:
            raise ValueError("Calculator descriptor not found")
        return CalculatorDescriptorV1.model_validate(row.document)

    def _binding(
        self, request: QualityContractPublishRequest
    ) -> MetricCalculatorBindingV1:
        return self._binding_values(
            request.metric_key,
            request.metric_version,
            request.calculator_key,
            request.calculator_version,
        )

    def _binding_values(
        self,
        metric_key: str,
        metric_version: str,
        calculator_key: str,
        calculator_version: str,
    ) -> MetricCalculatorBindingV1:
        row = self.session.scalar(
            select(MetricCalculatorBindingModel).filter_by(
                metric_key=metric_key,
                metric_version=metric_version,
                calculator_key=calculator_key,
                calculator_version=calculator_version,
            )
        )
        if row is None:
            raise ValueError("Metric/Calculator binding not found")
        return MetricCalculatorBindingV1.model_validate(row.document)

    @staticmethod
    def _validate_threshold(value: Any, value_type: str) -> None:
        valid = {
            "NUMBER": isinstance(value, (int, float)) and not isinstance(value, bool),
            "STRING": isinstance(value, str),
            "BOOLEAN": isinstance(value, bool),
            "OBJECT": isinstance(value, (dict, list)),
        }[value_type]
        if not valid:
            raise ValueError(
                "Rule threshold type does not match measurement value_type"
            )

    @staticmethod
    def _validate_parameters(
        parameters: dict[str, Any], schema: dict[str, Any]
    ) -> None:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(parameters),
            key=lambda item: list(item.path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(item) for item in first.path) or "$"
            raise ValueError(
                f"Invalid Calculator parameters at {location}: {first.message}"
            )

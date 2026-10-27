from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.services.metric_registry_service import MetricRegistryService
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


class PublishedQualityContract(BaseModel):
    target_dataset_version_id: UUID
    contract: QualityContractVersionV1


class QualityContractService:
    def __init__(self, session: Session):
        self.session = session

    def publish(self, request: QualityContractPublishRequest) -> PublishedQualityContract:
        MetricRegistryService(self.session).seed()
        dataset_version = self.session.get(
            DatasetVersionModel, request.dataset_version_id
        )
        if dataset_version is None:
            raise ValueError("DatasetVersion not found")
        if dataset_version.status != "PUBLISHED":
            raise ValueError("Quality Contract requires a PUBLISHED DatasetVersion")
        if not dataset_version.artifact_refs or "manifest" not in dataset_version.artifact_refs:
            raise ValueError("Published DatasetVersion has no manifest artifact")

        metric = self._metric(request.metric_key, request.metric_version)
        calculator = self._calculator(
            request.calculator_key, request.calculator_version
        )
        binding = self._binding(request)
        dataset = self.session.get(DatasetModel, dataset_version.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found for selected version")
        if not set(metric.applicability.modalities).intersection(dataset.modality):
            raise ValueError("Dataset modality is incompatible with selected metric")
        mapping = dataset_version.artifact_refs.get("field_mapping", {})
        available_roles = {
            item.get("target", {}).get("role")
            for item in mapping.get("mappings", [])
            if item.get("action") == "MAP"
            and item.get("target", {}).get("kind") == "CONTENT"
        }
        required_roles = set(calculator.input_contract.role_selectors)
        if not required_roles.issubset(available_roles):
            missing = ", ".join(sorted(required_roles - available_roles))
            raise ValueError(f"DatasetVersion is missing required content roles: {missing}")
        measurement = next(
            (item for item in metric.measurements if item.key == request.measurement),
            None,
        )
        if measurement is None:
            raise ValueError("Unknown measurement for selected metric")
        if not binding.enabled:
            raise ValueError("Metric/Calculator binding is disabled")
        if request.measurement not in {item.key for item in metric.measurements}:
            raise ValueError("Measurement is not registered by the metric")
        self._validate_parameters(request.parameters, calculator.parameter_schema)
        self._validate_threshold(request.rule.threshold, measurement.value_type)

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
                "modalities": metric.applicability.modalities,
                "task_types": [],
            },
            "items": [
                {
                    "key": f"{request.metric_key}_{request.measurement}",
                    "metric": {
                        "key": request.metric_key,
                        "version": request.metric_version,
                        "measurement": request.measurement,
                    },
                    "scope": metric.supported_scopes[0],
                    "calculation": {
                        "parameters": request.parameters,
                        "implementation_constraints": {
                            "deterministic": calculator.deterministic,
                            "attributes": {
                                "calculator_key": request.calculator_key,
                                "calculator_version": request.calculator_version,
                                "binding_priority": binding.priority,
                            },
                        },
                    },
                    "rule": {
                        "operator": request.rule.operator,
                        "threshold": request.rule.threshold,
                    },
                    "severity": request.rule.severity,
                    "on_missing": request.rule.on_missing,
                }
            ],
            "gate": {
                "unknown_policy": "UNKNOWN",
                "fail_on_severities": ["WARNING", "ERROR", "CRITICAL"],
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
        row = self.session.scalar(
            select(MetricCalculatorBindingModel).filter_by(
                metric_key=request.metric_key,
                metric_version=request.metric_version,
                calculator_key=request.calculator_key,
                calculator_version=request.calculator_version,
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
            raise ValueError("Rule threshold type does not match measurement value_type")

    @staticmethod
    def _validate_parameters(parameters: dict[str, Any], schema: dict[str, Any]) -> None:
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(parameters) - set(properties))
            if unknown:
                raise ValueError(f"Unknown Calculator parameters: {', '.join(unknown)}")
        for name, value in parameters.items():
            definition = properties.get(name, {})
            expected = definition.get("type")
            valid = {
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                None: True,
            }.get(expected, False)
            if not valid:
                raise ValueError(f"Calculator parameter {name} must be {expected}")
            if "minimum" in definition and value < definition["minimum"]:
                raise ValueError(f"Calculator parameter {name} is below minimum")
            if "maximum" in definition and value > definition["maximum"]:
                raise ValueError(f"Calculator parameter {name} exceeds maximum")

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.contracts.quality import (
    NativeEvaluationItemV1,
    NativeStatisticRef,
    QualityContractVersionV1,
    Severity,
    ThresholdRule,
)
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
    strategy: Literal["THRESHOLD", "OBSERVE_ONLY"] = "THRESHOLD"
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NE"] | None = None
    threshold: Any = None
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


class ObservationItemPublishRequest(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal["1.0"]
    key: str = Field(min_length=1, max_length=255)
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    calculator_key: str = Field(min_length=1)
    calculator_version: str = Field(min_length=1)
    strategy: Literal["OBSERVE_ONLY"]
    projections: list[dict] = Field(max_length=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class NativeEvaluationItemPublishRequest(ObservationItemPublishRequest):
    schema_version: Literal["1.1"]
    strategy: Literal["EVALUATE"]
    statistic_ref: NativeStatisticRef
    rule: ThresholdRule
    severity: Severity


class ContractGateRequest(BaseModel):
    unknown_policy: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    fail_on_severities: list[Literal["INFO", "WARNING", "ERROR", "CRITICAL"]] = Field(
        default_factory=lambda: ["WARNING", "ERROR", "CRITICAL"], min_length=1
    )


class QualityContractMultiPublishRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=64)
    dataset_version_id: UUID
    items: list[QualityContractItemPublishRequest] = Field(default_factory=list)
    observation_items: list[
        Annotated[
            ObservationItemPublishRequest | NativeEvaluationItemPublishRequest,
            Field(discriminator="strategy"),
        ]
    ] = Field(default_factory=list)
    gate: ContractGateRequest = Field(default_factory=ContractGateRequest)

    @model_validator(mode="after")
    def item_keys_are_unique(self) -> QualityContractMultiPublishRequest:
        keys = [
            item.key or f"{item.metric_key}_{item.measurement}" for item in self.items
        ]
        keys.extend(item.key for item in self.observation_items)
        if not keys:
            raise ValueError("contract requires at least one execution item")
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
        request = self._resolve_field_relation_items(request)
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

        observations = [
            self._validate_observation(context, item)
            for item in request.observation_items
        ]
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
                        for metric in [
                            *(entry[0] for entry in validated),
                            *(entry[0] for entry in observations),
                        ]
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
                    "rule": item.rule.model_dump(
                        mode="json", include={"strategy", "operator", "threshold"}
                    ),
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
        if observations:
            body["schema_version"] = "1.1"
            body["observation_items"] = [entry[1] for entry in observations]
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

    @staticmethod
    def _resolve_field_relation_items(request):
        # Standard user-facing Metric items carry measurement thresholds. This
        # Calculator persists their facts in its existing native result slot.
        items = []
        observations = list(request.observation_items)
        for item in request.items:
            if (item.metric_key, item.metric_version) != ("0207", "2.0.0"):
                items.append(item)
                continue
            if item.rule.strategy != "THRESHOLD":
                raise ValueError("0207@2.0.0 requires a numeric threshold rule")
            if item.rule.on_missing != "UNKNOWN":
                raise ValueError(
                    "0207@2.0.0 requires UNKNOWN handling for missing statistics"
                )
            key = item.key or f"{item.metric_key}_{item.measurement}"
            observations.append(
                NativeEvaluationItemPublishRequest(
                    schema_version="1.1",
                    key=key,
                    strategy="EVALUATE",
                    metric_key=item.metric_key,
                    metric_version=item.metric_version,
                    calculator_key=item.calculator_key,
                    calculator_version=item.calculator_version,
                    parameters=item.parameters,
                    projections=[],
                    statistic_ref=NativeStatisticRef(
                        contract_item_key=key,
                        output_slot_id=key,
                        calculator_version=item.calculator_version,
                        statistics_key=item.measurement,
                    ),
                    rule=ThresholdRule(
                        operator=item.rule.operator, threshold=item.rule.threshold
                    ),
                    severity=item.rule.severity,
                )
            )
        field_relations = [
            item
            for item in observations
            if (item.metric_key, item.metric_version) == ("0207", "2.0.0")
        ]
        if field_relations and any(item.metric_key == "0207" for item in items):
            raise ValueError("select one 0207 version per Contract")
        if any(
            (item.metric_key, item.metric_version) == ("0206", "1.0.0")
            and item.rule.strategy == "THRESHOLD"
            for item in items
        ) and any(
            isinstance(relation, dict) and relation.get("method") == "semantic_match"
            for item in field_relations
            if isinstance(item.parameters.get("relations"), list)
            for relation in item.parameters["relations"]
        ):
            raise ValueError(
                "0206@1.0.0 overlaps with 0207 semantic_match; select one for threshold evaluation"
            )
        if len(items) == len(request.items):
            return request
        return QualityContractMultiPublishRequest.model_validate(
            request.model_dump(exclude={"items", "observation_items"})
            | {"items": items, "observation_items": observations}
        )

    def _validate_observation(self, context, item: ObservationItemPublishRequest):
        metric = self._metric(item.metric_key, item.metric_version)
        calculator = self._calculator(item.calculator_key, item.calculator_version)
        binding = self._binding_values(
            item.metric_key,
            item.metric_version,
            item.calculator_key,
            item.calculator_version,
        )
        if (
            not binding.enabled
            or not binding.native_only
            or calculator.native_output is None
        ):
            raise ValueError(
                "observation requires enabled native binding with zero projections"
            )
        if (
            "DATASET" not in calculator.capabilities.scopes
            or "DATASET" not in metric.supported_scopes
        ):
            raise ValueError("observation requires dataset scope")
        parameters = self._resolved_parameters(
            calculator.parameter_schema, item.parameters
        )
        error = next(
            Draft202012Validator(
                calculator.parameter_schema, format_checker=FormatChecker()
            ).iter_errors(parameters),
            None,
        )
        if error is not None:
            raise ValueError(f"Invalid Calculator parameters: {error.message}")
        if (item.calculator_key, item.calculator_version) == (
            "field_relation_consistency",
            "2.0.0",
        ):
            from qa_eval.calculators.field_relation_consistency.configuration import (
                settings,
            )
            from qa_eval.calculators.protocol import CalculatorValidationError

            try:
                settings(parameters)
            except CalculatorValidationError as exc:
                raise ValueError(
                    f"Invalid field relation configuration: {exc}"
                ) from exc
        decision = ApplicabilityService().evaluate(
            context, metric, calculator, binding, parameters
        )
        if decision.status != "APPLICABLE":
            codes = ", ".join(value.code for value in decision.findings)
            raise ValueError(f"Native Calculator is {decision.status}: {codes}")
        document = {
            "schema_version": item.schema_version,
            "key": item.key,
            "metric": {"key": item.metric_key, "version": item.metric_version},
            "scope": "DATASET",
            "strategy": item.strategy,
            "projections": item.projections,
            "calculation": {
                "parameters": parameters,
                "implementation_constraints": {
                    "deterministic": calculator.deterministic,
                    "attributes": {
                        "calculator_key": item.calculator_key,
                        "calculator_version": item.calculator_version,
                    },
                },
            },
        }

        if isinstance(item, NativeEvaluationItemPublishRequest):
            schema = calculator.native_output
            statistic = next(
                (
                    stat
                    for stat in schema.statistics
                    if stat.key == item.statistic_ref.statistics_key
                ),
                None,
            )
            if statistic is None:
                raise ValueError("native rule references an unknown statistics key")
            document.update(
                statistic_ref=item.statistic_ref,
                rule=item.rule,
                severity=item.severity,
                statistic=statistic,
                output_schema_version=schema.schema_version,
                output_schema_fingerprint=NativeEvaluationItemV1.schema_fingerprint(
                    schema
                ),
            )
            document = NativeEvaluationItemV1.model_validate(document).model_dump(
                mode="json"
            )
        return metric, document

    def validate_item(self, context, item: QualityContractItemPublishRequest):
        metric = self._metric(item.metric_key, item.metric_version)
        calculator = self._calculator(item.calculator_key, item.calculator_version)
        binding = self._binding_values(
            item.metric_key,
            item.metric_version,
            item.calculator_key,
            item.calculator_version,
        )
        if binding.native_only or calculator.native_output is not None:
            raise ValueError("native Calculator requires an observation item")
        measurement = next(
            (value for value in metric.measurements if value.key == item.measurement),
            None,
        )
        if measurement is None:
            raise ValueError("Unknown measurement for selected metric")
        if (
            binding.produced_measurements
            and item.measurement not in binding.produced_measurements
        ):
            raise ValueError(
                "Selected Calculator does not produce the requested measurement"
            )
        item.parameters = self._resolved_parameters(
            calculator.parameter_schema, item.parameters
        )
        self._validate_parameters(item.parameters, calculator.parameter_schema)
        if item.rule.strategy == "THRESHOLD":
            self._validate_threshold(item.rule.threshold, measurement.value_type)
            if (
                measurement.minimum is not None
                and item.rule.threshold < measurement.minimum
            ):
                raise ValueError("Rule threshold is below the measurement minimum")
            if (
                measurement.maximum is not None
                and item.rule.threshold > measurement.maximum
            ):
                raise ValueError("Rule threshold exceeds the measurement maximum")
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
    def _resolved_parameters(
        schema: dict[str, Any], configured: dict[str, Any]
    ) -> dict[str, Any]:
        resolved = {
            key: specification["default"]
            for key, specification in schema.get("properties", {}).items()
            if "default" in specification
        }
        resolved.update(configured)
        return resolved

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

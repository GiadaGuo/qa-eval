from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from packaging.version import Version
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.evaluation.operational_scoring import load_approved_business_scoring_profile
from qa_eval.application.normalization.consistency_input_slots import consistency_input_contract
from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.contracts.logical_inputs import InputSelector
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
    mapping_digest: str | None = None
    input_selectors: dict[str, InputSelector] = Field(default_factory=dict)
    rule: ContractRuleRequest


class QualityContractItemPublishRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=255)
    metric_key: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    measurement: str | None = Field(default=None, min_length=1)
    calculator_key: str | None = Field(default=None, min_length=1)
    calculator_version: str | None = Field(default=None, min_length=1)
    operator_slot: str | None = Field(default=None, min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    mapping_digest: str | None = None
    input_selectors: dict[str, InputSelector] = Field(default_factory=dict)
    rule: ContractRuleRequest


    @model_validator(mode="after")
    def implementation_selector(self):
        if bool(self.calculator_key) != bool(self.calculator_version):
            raise ValueError("Calculator key and version must be supplied together")
        if self.operator_slot is None and not (self.measurement and self.calculator_key):
            raise ValueError("Specify a measurement and Calculator or an operator slot")
        return self


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
    coverage_min: float | None = Field(default=None, strict=True, ge=0, le=1)
    mapping_digest: str | None = None
    input_selectors: dict[str, InputSelector] = Field(default_factory=dict)


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
    excluded_metrics: dict[str, str] | None = None
    scoring_weights: dict[str, Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    gate: ContractGateRequest = Field(default_factory=ContractGateRequest)

    @model_validator(mode="after")
    def item_keys_are_unique(self) -> QualityContractMultiPublishRequest:
        keys = [
            item.key or f"{item.metric_key}_{item.operator_slot or item.measurement}" for item in self.items
        ]
        keys.extend(item.key for item in self.observation_items)
        if self.excluded_metrics is not None and any(
            not reason.strip() for reason in self.excluded_metrics.values()
        ):
            raise ValueError("请填写所有不参与评测的原因")
        if not keys:
            raise ValueError("contract requires at least one execution item")
        if len(keys) != len(set(keys)):
            raise ValueError("contract item keys must be unique")
        return self


class PublishedQualityContract(BaseModel):
    target_dataset_version_id: UUID | None = None
    document_version_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
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
                        mapping_digest=request.mapping_digest,
                        input_selectors=request.input_selectors,
                        rule=request.rule,
                    )
                ],
            )
        )

    def publish_multi(
        self, request: QualityContractMultiPublishRequest, *, preview: bool = False
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
            or not {"manifest", "raw_record_manifest"}.intersection(dataset_version.artifact_refs)
        ):
            raise ValueError("Published DatasetVersion has no manifest artifact")

        dataset = self.session.get(DatasetModel, dataset_version.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found for selected version")

        sample_schema = "3.0" if "raw_record_manifest" in dataset_version.artifact_refs else "2.0"
        request = request.model_copy(update={"items": [
            self._resolve_operator_item(item, sample_schema) if item.operator_slot else item
            for item in request.items
        ]})
        request = self._resolve_field_relation_items(request)
        context, _ = ApplicabilityService.context_for(
            self.session, dataset_version, dataset
        )
        validated = [self.validate_item(context, item) for item in request.items]
        raw = "raw_record_manifest" in dataset_version.artifact_refs
        for item, (_, calculator, _, _, _) in zip(request.items, validated, strict=True):
            if not (item.mapping_digest or item.input_selectors):
                continue
            logical = consistency_input_contract(calculator, item.parameters)
            if not raw or logical is None:
                raise ValueError("Input selectors and mapping revisions require a v3 Calculator")
            if set(item.input_selectors) - {slot.name for slot in logical.slots if slot.selectable}:
                raise ValueError("Input selector supplied for a fixed or unknown slot")


        slots = [
            (metric.metric_key, metric.version, binding.compatibility["operator_slot"])
            for metric, _, binding, _, _ in validated
            if "operator_slot" in binding.compatibility
        ]
        if slots and any(
            (item.metric_key == "0208" and item.metric_version != "2.0.0")
            or item.metric_key.startswith("020801")
            for item in request.items
        ):
            raise ValueError("0208 operator slots and legacy cleanliness metrics overlap")
        if len(slots) != len(set(slots)):
            raise ValueError("Select one Calculator implementation per operator slot")

        observations = [
            self._validate_observation(context, item)
            for item in request.observation_items
        ]
        if request.excluded_metrics is not None:
            required = {f"020{index}" for index in range(1, 9)}
            selected = {
                metric.parent_metric_key or metric.metric_key
                for metric, *_ in [*validated, *observations]
            }
            excluded = set(request.excluded_metrics)
            if selected & excluded or selected | excluded != required:
                raise ValueError("0201–0208 均须配置算子或显式排除")
        now = datetime.now(UTC)
        contract_id = uuid7()
        contract_version_id = uuid7()
        body = {
            "schema_version": "1.0",
            "contract_id": contract_id,
            "contract_version_id": contract_version_id,
            "version_label": request.version_label,
            "name": request.name,
            "status": "DRAFT" if preview else "PUBLISHED",
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
                                **({"mapping_digest": item.mapping_digest} if item.mapping_digest else {}),
                                **({"input_selectors": {
                                    key: selector.model_dump(mode="json", exclude_none=True)
                                    for key, selector in item.input_selectors.items()
                                }} if item.input_selectors else {}),
                                **{
                                    field: binding.compatibility[field]
                                    for field in ("operator_slot", "execution_grain")
                                    if field in binding.compatibility
                                },
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
            "published_at": None if preview else now,
        }
        if request.excluded_metrics is not None:
            body["excluded_metrics"] = request.excluded_metrics
        if observations:
            body["schema_version"] = "1.1"
            body["observation_items"] = [entry[1] for entry in observations]
        profile = self._scoring_profile(request, validated, observations)
        if profile is not None:
            body["scoring_profile"] = profile
        canonical = json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))
        body["fingerprint"] = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        contract = QualityContractVersionV1.model_validate(body)
        if preview:
            return PublishedQualityContract(
                target_dataset_version_id=request.dataset_version_id, contract=contract
            )
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

    def _resolve_operator_item(self, item: QualityContractItemPublishRequest, sample_schema):
        rows = self.session.scalars(select(MetricCalculatorBindingModel).filter_by(
            metric_key=item.metric_key, metric_version=item.metric_version, enabled=True,
        ))
        candidates = []
        for row in rows:
            binding = MetricCalculatorBindingV1.model_validate(row.document)
            if binding.compatibility.get("operator_slot") != item.operator_slot:
                continue
            if item.calculator_key and (binding.calculator_key, binding.calculator_version) != (
                item.calculator_key, item.calculator_version,
            ):
                continue
            descriptor = self._calculator(binding.calculator_key, binding.calculator_version)
            if (
                descriptor.availability.status == "AVAILABLE"
                and descriptor.input_contract.sample_record_schema == sample_schema
            ):
                candidates.append(binding)
        if not candidates:
            raise ValueError(f"No available Calculator for operator slot {item.operator_slot}")
        chosen = max(candidates, key=lambda binding: (
            binding.priority, Version(binding.calculator_version), binding.calculator_key,
        ))
        return item.model_copy(update={
            "key": item.key or f"{item.metric_key}_{item.operator_slot}",
            "calculator_key": chosen.calculator_key,
            "calculator_version": chosen.calculator_version,
            "measurement": item.measurement or chosen.compatibility["primary_measurement"],
        })

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
                    mapping_digest=item.mapping_digest,
                    input_selectors=item.input_selectors,
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
        if item.calculator_key == "field_relation_consistency" and item.calculator_version in {"2.0.0", "2.1.0"}:
            from qa_eval.calculators.field_relation_consistency.configuration import (
                settings,
            )
            from qa_eval.calculators.protocol import CalculatorValidationError

            from qa_eval.calculators.field_relation_consistency_v3 import resolved_settings

            try:
                validator = resolved_settings if item.calculator_version == "2.1.0" else settings
                validator(parameters)
            except CalculatorValidationError as exc:
                raise ValueError(
                    f"Invalid field relation configuration: {exc}"
                ) from exc
        if item.mapping_digest or item.input_selectors:
            logical = consistency_input_contract(calculator, item.parameters)
            if logical is None or calculator.input_contract.sample_record_schema != "3.0":
                raise ValueError("Input selectors and mapping revisions require a v3 Calculator")
            if set(item.input_selectors) - {slot.name for slot in logical.slots if slot.selectable}:
                raise ValueError("Input selector supplied for a fixed or unknown slot")
        decision = ApplicabilityService().evaluate(
            context, metric, calculator, binding, parameters
        )
        if decision.status != "APPLICABLE":
            codes = ", ".join(value.code for value in decision.findings)
            raise ValueError(f"Native Calculator is {decision.status}: {codes}")
        document = {
            "coverage_policy": {
                "coverage_min": 0.8 if item.coverage_min is None else item.coverage_min,
                "source": "SYSTEM_DEFAULT" if item.coverage_min is None else "CONTRACT_SLOT",
            },
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
                        **({"mapping_digest": item.mapping_digest} if item.mapping_digest else {}),
                        **({"input_selectors": {
                            key: selector.model_dump(mode="json")
                            for key, selector in item.input_selectors.items()
                        }} if item.input_selectors else {}),
                    },
                },
            },
        }

        if isinstance(item, NativeEvaluationItemPublishRequest):
            primary = binding.compatibility.get("primary_measurement")
            if binding.compatibility.get("default_rule") and item.statistic_ref.statistics_key != primary:
                raise ValueError(f"native rule must use primary measurement {primary}")
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
        primary = binding.compatibility.get("primary_measurement")
        if primary is not None and item.measurement != primary:
            raise ValueError("Contract item must select the operator's primary measurement")
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

    @staticmethod
    def _scoring_profile(request, validated, observations):
        entries = {}
        for item, (metric, *_rest) in zip(request.items, validated, strict=True):
            entries.setdefault(metric.metric_key, (metric, item.measurement))
        for metric, item in observations:
            if item["strategy"] == "EVALUATE":
                entries.setdefault(metric.metric_key, (metric, item["statistic_ref"]["statistics_key"]))
        entries = {
            key: entry for key, entry in entries.items()
            if key[:4] in {f"020{i}" for i in range(1, 9)}
        }
        if set(request.scoring_weights) - entries.keys():
            raise ValueError("评分权重只能配置给适用的评测指标")
        if not entries:
            return None
        approved = load_approved_business_scoring_profile()
        approved_mappings = {item.metric_id: item for item in approved.metrics} if approved else {}
        sizes = Counter(key[:4] for key in entries)
        metrics = []
        for key, (metric, measurement_key) in sorted(entries.items()):
            configs = [m for m in metric.measurements if m.key == measurement_key]
            direction = configs[0].direction if configs and (configs[0].minimum, configs[0].maximum) == (0, 1) else None
            if direction not in ("HIGHER_IS_BETTER", "LOWER_IS_BETTER"):
                direction = None
            axis = key[:4]
            mapping = approved_mappings.get(key)
            if mapping and (
                mapping.measurement_key != measurement_key
                or mapping.metric_version not in {None, metric.version}
                or mapping.dimension_id != axis
            ):
                mapping = None
            weight = request.scoring_weights.get(key)
            if mapping:
                metrics.append({
                    **mapping.model_dump(mode="json"),
                    "metric_version": metric.version,
                    "weight": weight if weight is not None else mapping.weight,
                    "weight_source": "USER" if weight is not None else "APPROVED",
                })
                continue
            metrics.append({
                "metric_id": key,
                "metric_version": metric.version,
                "measurement_key": measurement_key,
                "scoring_mapping_version": "registry-ratio-v1" if direction else "unmapped-v1",
                "scoring_method": f"RATIO_{direction}" if direction else "NONE",
                "direction": direction or "UNSCORED",
                "valid_range": {"minimum": 0, "maximum": 1} if direction else None,
                "weight": request.scoring_weights.get(key, 1 / (len(sizes) * sizes[axis])),
                "weight_source": "USER" if key in request.scoring_weights else "EQUAL",
                "dimension_id": axis,
                "eligibility_policy": "FULL_RESULT",
                "aggregation_method": "WEIGHTED_MEAN",
            })
        return {
            "profile_id": "DATA_QUALITY_BUSINESS",
            "profile_version": "1.0.0",
            "profile_type": "BUSINESS",
            "aggregation_method": "WEIGHTED_MEAN",
            "metrics": metrics,
            "disclaimer": (
                "权重来源依次为用户配置、已批准配置和系统默认。"
                "系统默认按适用维度等权，维度内指标等权。"
                + (f"已批准配置：{approved.profile_id}@{approved.profile_version}。" if approved else "")
            ),
        }

    def publish_document(self, request):
        from qa_eval.application.services.document_evaluation_target import contract_template

        body = contract_template(self, request)
        canonical = json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))
        body["fingerprint"] = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        contract = QualityContractVersionV1.model_validate(body)
        row = QualityContractVersionModel(
            contract_version_id=contract.contract_version_id,
            contract_id=contract.contract_id,
            document_version_id=request.document_version_id,
            version_label=contract.version_label,
            name=contract.name,
            status=contract.status,
            fingerprint=contract.fingerprint,
            document=contract.model_dump(mode="json"),
            published_at=contract.published_at,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_contract(row)

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
            document_version_id=row.document_version_id,
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

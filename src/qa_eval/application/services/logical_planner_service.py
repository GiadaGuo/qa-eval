from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any, ClassVar

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.contracts.execution import LogicalEvaluationPlanV1, PlanningDiagnosticV1
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.registry import (
    CalculatorImplementationModel,
    MetricCalculatorBindingModel,
    MetricDefinitionModel,
)
from qa_eval.infrastructure.uuid7 import uuid7


class LogicalPlanningError(ValueError):
    def __init__(self, diagnostics: list[PlanningDiagnosticV1]):
        self.diagnostics = diagnostics
        summary = "; ".join(f"{item.code}: {item.message}" for item in diagnostics)
        super().__init__(summary or "Logical planning failed")


def semantic_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


class LogicalPlannerService:
    """Compile a published Quality Contract into an execution-backend-neutral DAG."""

    COMPILER: ClassVar[dict[str, str]] = {
        "key": "qa-eval-logical-planner",
        "version": "1.0.0",
    }
    SHARING_KEYS: ClassVar[tuple[str, ...]] = (
        "provider",
        "model",
        "sampling_fingerprint",
        "prompt_family",
        "preprocessing_fingerprint",
        "context_policy",
        "privacy_policy",
        "input_selector",
    )

    def __init__(self, session: Session):
        self.session = session

    def compile(
        self,
        contract: QualityContractVersionV1,
        dataset_version: DatasetVersionModel,
    ) -> LogicalEvaluationPlanV1:
        diagnostics: list[PlanningDiagnosticV1] = []
        if contract.status != "PUBLISHED":
            self._error(
                diagnostics,
                "CONTRACT_NOT_PUBLISHED",
                "Quality Contract must be PUBLISHED",
            )
        if dataset_version.status != "PUBLISHED":
            self._error(
                diagnostics,
                "DATASET_VERSION_NOT_PUBLISHED",
                "Dataset Version must be PUBLISHED",
            )

        modalities, roles = self._dataset_capabilities(dataset_version)
        profile = self.session.scalar(
            select(DatasetProfileModel)
            .where(DatasetProfileModel.dataset_version_id == dataset_version.id)
            .order_by(DatasetProfileModel.created_at.desc())
        )
        operations: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        share_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for index, item in enumerate(contract.items):
            metric = self._metric(item.metric.key, item.metric.version)
            calculator_key = str(
                item.calculation.implementation_constraints.attributes.get(
                    "calculator_key", ""
                )
            )
            calculator_version = str(
                item.calculation.implementation_constraints.attributes.get(
                    "calculator_version", ""
                )
            )
            calculator = self._calculator(calculator_key, calculator_version)
            binding = self._binding(
                item.metric.key, item.metric.version, calculator_key, calculator_version
            )
            item_errors = len(diagnostics)
            if metric is None:
                self._error(
                    diagnostics,
                    "METRIC_NOT_REGISTERED",
                    "Metric Definition is not registered",
                    item.key,
                    metric_key=item.metric.key,
                    metric_version=item.metric.version,
                )
            if calculator is None:
                self._error(
                    diagnostics,
                    "CALCULATOR_NOT_REGISTERED",
                    "Calculator Implementation is not registered",
                    item.key,
                    calculator_key=calculator_key,
                    calculator_version=calculator_version,
                )
            elif (
                calculator.availability.status != "AVAILABLE"
                or not calculator.entrypoint
            ):
                self._error(
                    diagnostics,
                    "CALCULATOR_NOT_EXECUTABLE",
                    "Calculator is unavailable, deprecated, or a placeholder",
                    item.key,
                    availability=calculator.availability.status,
                    reason=calculator.availability.reason,
                )
            if binding is None:
                self._error(
                    diagnostics,
                    "BINDING_NOT_REGISTERED",
                    "Metric/Calculator binding is not registered",
                    item.key,
                )
            elif not binding.enabled:
                self._error(
                    diagnostics,
                    "BINDING_DISABLED",
                    "Metric/Calculator binding is disabled",
                    item.key,
                )
            if metric is None or calculator is None or binding is None:
                continue
            if not set(metric.applicability.modalities).intersection(modalities):
                self._error(
                    diagnostics,
                    "MODALITY_MISMATCH",
                    "Metric does not support the Dataset modalities",
                    item.key,
                    required=metric.applicability.modalities,
                    actual=sorted(modalities),
                )
            missing_roles = sorted(
                set(
                    metric.applicability.required_roles
                    + calculator.input_contract.role_selectors
                )
                - roles
            )
            if missing_roles:
                self._error(
                    diagnostics,
                    "REQUIRED_ROLE_MISSING",
                    "Dataset is missing required content roles",
                    item.key,
                    roles=missing_roles,
                )
            if item.scope not in calculator.capabilities.scopes:
                self._error(
                    diagnostics,
                    "SCOPE_MISMATCH",
                    "Calculator does not support the Contract scope",
                    item.key,
                    scope=item.scope,
                )
            parameter_errors = sorted(
                Draft202012Validator(calculator.parameter_schema).iter_errors(
                    item.calculation.parameters
                ),
                key=lambda error: list(error.path),
            )
            if parameter_errors:
                self._error(
                    diagnostics,
                    "INVALID_CALCULATOR_PARAMETERS",
                    parameter_errors[0].message,
                    item.key,
                )
            if len(diagnostics) > item_errors:
                continue

            operation_id = f"metric-{index}-{item.metric.key}"
            dependencies = [
                {"kind": "DATASET_ARTIFACT", "key": value, "required": True}
                for value in calculator.input_artifact_requirements
            ]
            dependencies.extend(
                {"kind": "CONTENT_ROLE", "key": role, "required": True}
                for role in sorted(
                    set(
                        metric.applicability.required_roles
                        + calculator.input_contract.role_selectors
                    )
                )
            )
            if bool(calculator.applicability_constraints.get("requires_profile")):
                dependencies.append(
                    {"kind": "DATASET_PROFILE", "key": "latest", "required": True}
                )
                if profile is None:
                    self._error(
                        diagnostics,
                        "DATASET_PROFILE_MISSING",
                        "Calculator requires a Dataset Profile",
                        item.key,
                    )
                    continue
            output = {
                "contract_item_key": item.key,
                "metric": {"key": item.metric.key, "version": item.metric.version},
                "measurement": item.metric.measurement,
                "output_kind": "METRIC_RESULT",
            }
            operation = {
                "operation_id": operation_id,
                "operation_type": "METRIC",
                "metric": {"key": item.metric.key, "version": item.metric.version},
                "calculator_binding": {
                    "calculator": {
                        "key": calculator.calculator_key,
                        "version": calculator.version,
                    },
                    "deterministic": calculator.deterministic,
                    "modalities": calculator.capabilities.modalities,
                    "scopes": calculator.capabilities.scopes,
                    "availability": calculator.availability.status,
                },
                "parameters": item.calculation.parameters,
                "input_dependencies": dependencies,
                "depends_on": [],
                "output_bindings": [output],
                "sharing_constraints": {
                    **item.calculation.parameters.get("sharing", {}),
                    "execution_mode": calculator.execution.mode,
                    "operator_type": calculator.execution.operator_type,
                    "output_field": calculator.execution.output_field,
                    "rubric_fingerprint": semantic_fingerprint(
                        calculator.execution.rubric or {}
                    ),
                },
            }
            operations.append(operation)
            outputs.append(output)
            share_key = self._sharing_key(
                item.metric.key, calculator, item.calculation.parameters, dependencies
            )
            if share_key is not None:
                share_candidates[share_key].append(operation)

        if any(item.severity == "ERROR" for item in diagnostics):
            raise LogicalPlanningError(diagnostics)

        groups = self._build_shared_groups(share_candidates, operations, outputs)
        edges = sorted(
            (dependency, op["operation_id"])
            for op in operations
            for dependency in op["depends_on"]
        )
        self.validate_dag([op["operation_id"] for op in operations], edges)
        semantic = {
            "dataset_version_id": str(dataset_version.id),
            "contract_version_id": str(contract.contract_version_id),
            "compiler": self.COMPILER,
            "operations": operations,
            "dependency_edges": edges,
            "shared_computation_groups": groups,
            "output_bindings": outputs,
        }
        return LogicalEvaluationPlanV1.model_validate(
            {
                "logical_plan_id": uuid7(),
                "dataset_version_id": dataset_version.id,
                "contract_id": contract.contract_id,
                "contract_version_id": contract.contract_version_id,
                "compiler": self.COMPILER,
                **{
                    key: semantic[key]
                    for key in (
                        "operations",
                        "dependency_edges",
                        "shared_computation_groups",
                        "output_bindings",
                    )
                },
                "diagnostics": diagnostics,
                "semantic_fingerprint": semantic_fingerprint(semantic),
                "created_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def validate_dag(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str]:
        known = set(node_ids)
        if len(known) != len(node_ids):
            raise LogicalPlanningError(
                [
                    PlanningDiagnosticV1(
                        severity="ERROR",
                        code="DUPLICATE_OPERATION_ID",
                        message="Logical operation IDs must be unique",
                    )
                ]
            )
        incoming = {node: 0 for node in node_ids}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for source, target in edges:
            if source not in known or target not in known:
                raise LogicalPlanningError(
                    [
                        PlanningDiagnosticV1(
                            severity="ERROR",
                            code="UNKNOWN_DEPENDENCY",
                            message="Dependency edge references an unknown operation",
                            details={"source": source, "target": target},
                        )
                    ]
                )
            outgoing[source].append(target)
            incoming[target] += 1
        queue = deque(sorted(node for node, degree in incoming.items() if degree == 0))
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for target in sorted(outgoing[node]):
                incoming[target] -= 1
                if incoming[target] == 0:
                    queue.append(target)
        if len(result) != len(node_ids):
            raise LogicalPlanningError(
                [
                    PlanningDiagnosticV1(
                        severity="ERROR",
                        code="DEPENDENCY_CYCLE",
                        message="Logical operation dependencies contain a cycle",
                    )
                ]
            )
        return result

    def _build_shared_groups(self, candidates, operations, outputs):
        groups = []
        for index, (fingerprint, members) in enumerate(sorted(candidates.items())):
            if len(members) < 2:
                continue
            shared_id = f"shared-llm-{index}"
            shared_operation_id = f"operation-{shared_id}"
            member_ids = [member["operation_id"] for member in members]
            constraints = {
                key: value
                for key, value in members[0]["sharing_constraints"].items()
                if key not in {"output_field", "rubric_fingerprint"}
            }
            shared = {
                "operation_id": shared_operation_id,
                "operation_type": "SHARED_SEMANTIC",
                "parameters": constraints,
                "input_dependencies": members[0]["input_dependencies"],
                "depends_on": [],
                "output_bindings": [],
                "sharing_constraints": constraints,
            }
            for member in members:
                member["depends_on"] = [shared_operation_id]
                for output in member["output_bindings"]:
                    output["shared_computation_id"] = shared_id
                shared["output_bindings"].extend(member["output_bindings"])
            output_by_item = {
                item["contract_item_key"]: item for item in shared["output_bindings"]
            }
            for output in outputs:
                if output["contract_item_key"] in output_by_item:
                    output["shared_computation_id"] = shared_id
            operations.insert(0, shared)
            groups.append(
                {
                    "shared_computation_id": shared_id,
                    "operation_id": shared_operation_id,
                    "member_operation_ids": member_ids,
                    "metric_keys": [member["metric"]["key"] for member in members],
                    "compatibility_fingerprint": fingerprint,
                    "source_identity": shared_id,
                }
            )
        return groups

    def _sharing_key(self, metric_key, calculator, parameters, dependencies):
        if (
            calculator.execution.mode != "SHARED_INFERENCE"
            or calculator.runtime.kind != "PYTHON_IN_PROCESS"
        ):
            return None
        sharing = parameters.get("sharing")
        if (
            not isinstance(sharing, dict)
            or sharing.get("allow_shared_execution") is not True
        ):
            return None
        capability = calculator.execution
        if (
            sharing.get("prompt_family") != capability.prompt_family
            or capability.shareability_constraints.get("rubric_composable") is not True
            or capability.shareability_constraints.get("independent_output") is not True
        ):
            return None
        if not all(sharing.get(key) for key in self.SHARING_KEYS):
            return None
        if sharing.get("independent_outputs") is not True:
            return None
        payload = {
            "dependencies": dependencies,
            "operator_type": calculator.execution.operator_type,
            "content_role": parameters.get("content_role"),
            "sample_limit": parameters.get("sample_limit"),
            "max_input_chars": parameters.get("max_input_chars"),
            **{key: sharing[key] for key in self.SHARING_KEYS},
        }
        fingerprint = semantic_fingerprint(payload)
        # Retained on the operation for explainable grouping, not execution tuning.
        return fingerprint

    def _dataset_capabilities(self, version):
        refs = version.artifact_refs or {}
        mapping = refs.get("field_mapping", {})
        roles = {
            str(item.get("target", {}).get("role"))
            for item in mapping.get("mappings", [])
            if item.get("action") == "MAP" and item.get("target", {}).get("role")
        }
        modalities = {
            str(item.get("target", {}).get("modality"))
            for item in mapping.get("mappings", [])
            if item.get("target", {}).get("modality")
        }
        if not modalities:
            modalities = {"text"} if roles else set()
        return modalities, roles

    def _metric(self, key, version):
        row = self.session.scalar(
            select(MetricDefinitionModel).filter_by(metric_key=key, version=version)
        )
        return None if row is None else MetricDefinitionV1.model_validate(row.document)

    def _calculator(self, key, version):
        row = self.session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key=key, version=version
            )
        )
        return (
            None if row is None else CalculatorDescriptorV1.model_validate(row.document)
        )

    def _binding(self, metric_key, metric_version, calculator_key, calculator_version):
        row = self.session.scalar(
            select(MetricCalculatorBindingModel).filter_by(
                metric_key=metric_key,
                metric_version=metric_version,
                calculator_key=calculator_key,
                calculator_version=calculator_version,
            )
        )
        return (
            None
            if row is None
            else MetricCalculatorBindingV1.model_validate(row.document)
        )

    @staticmethod
    def _error(diagnostics, code, message, item_key=None, **details):
        diagnostics.append(
            PlanningDiagnosticV1(
                severity="ERROR",
                code=code,
                message=message,
                contract_item_key=item_key,
                details=details,
            )
        )

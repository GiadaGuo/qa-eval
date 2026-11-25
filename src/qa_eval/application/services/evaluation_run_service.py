from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qa_eval.application.document_evaluation.parser import parse_document
from qa_eval.application.normalization.consistency_input_slots import (
    consistency_input_contract,
    resolve_consistency_representations,
)
from qa_eval.application.normalization.input_resolver import (
    ArtifactInput,
    FrozenInputPlan,
    FrozenManifest,
    InputResolver,
    Transform,
)
from qa_eval.application.normalization.jhsjk_article_normalizer import _html_to_text
from qa_eval.application.normalization.record_reader import (
    RawRecordManifest,
    read_records,
)
from qa_eval.application.services.document_evaluation_target import document_target
from qa_eval.application.services.logical_planner_service import LogicalPlannerService
from qa_eval.application.services.mapping_revision_service import MappingRevisionService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.application.snapshots import ProfileSnapshotService
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorError,
    CalculatorValidationError,
    NativeCalculatorOutput,
)
from qa_eval.config import get_settings
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
    CalculatorOutputSchemaV1,
    CalculatorResultV1,
)
from qa_eval.contracts.execution import (
    EvaluationRunV1,
    LogicalEvaluationPlanV1,
    PhysicalEvaluationPlanV1,
    PhysicalStageV1,
    StageRunV1,
)
from qa_eval.contracts.logical_inputs import InputSelector
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityContractVersionV1, QualityIssueV1
from qa_eval.contracts.registry import CalculatorDescriptorV1, MetricDefinitionV1
from qa_eval.domain.exceptions import EvaluationRunNotDeletableError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.inference import InferenceOperatorError, LLMSemanticJudgeOperator
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    CalculatorResultArtifactModel,
    CalculatorResultModel,
    EvaluationRunModel,
    LogicalEvaluationPlanModel,
    MetricResultModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    IssueIndexModel,
    QualityIssueModel,
    QualityReportModel,
    RuleEvaluationModel,
    ScoredQualityReportModel,
)
from qa_eval.infrastructure.db.models.registry import (
    CalculatorImplementationModel,
    MetricDefinitionModel,
)
from qa_eval.infrastructure.llm.client import (
    OpenAICompatibleLLMClient,
    StructuredLLMClient,
)
from qa_eval.infrastructure.uuid7 import uuid7

logger = logging.getLogger(__name__)
_TERMINAL_RUN_STATUSES = {"PARTIAL_SUCCEEDED", "SUCCEEDED", "FAILED", "CANCELED"}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: str | None) -> str:
    if not value:
        return f"sha256:{'0' * 64}"
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class EvaluationRunService:
    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        llm_client: StructuredLLMClient | None = None,
    ):
        self.session = session
        self.artifact_store = artifact_store
        self.calculator_loader = TrustedCalculatorLoader()
        self.llm_client = (
            llm_client
            if llm_client is not None
            else OpenAICompatibleLLMClient.from_settings(get_settings())
        )

    @staticmethod
    def _record_resolver() -> InputResolver:
        source = Path(__import__(_html_to_text.__module__, fromlist=[""]).__file__)
        return InputResolver([Transform(
            "html_to_plain@1.0.0", "sha256:" + _sha256_file(source),
            "text/html", "text/plain", _html_to_text,
        )])

    def preflight_inputs(
        self, version, calculator, *, mapping_digest=None, selectors=None, parameters=None,
    ) -> FrozenInputPlan:
        refs = version.artifact_refs or {}
        raw = refs.get("raw_record_manifest")
        contract = consistency_input_contract(calculator, parameters or {})
        if calculator.availability.status != "AVAILABLE":
            raise ValueError("DISABLED: Calculator is unavailable")
        if contract is None or contract.mode != "DIRECT":
            raise ValueError("CONTRACT_NOT_MIGRATED: Direct v3 Input Contract required")
        if raw is None or not self.artifact_store.exists(raw["source_uri"]):
            raise ValueError("ARTIFACT_MISSING: RAW_RECORDS")
        independent = set(calculator.input_artifact_requirements) - {"RAW_RECORDS"}
        declared = set(contract.artifact_kinds)
        if independent != declared:
            raise ValueError("INPUT_INCOMPATIBLE: independent artifact declarations disagree")
        artifacts = []
        for kind in contract.artifact_kinds:
            ref = refs.get("manifest") if kind == "DATASET_MANIFEST" else None
            if not isinstance(ref, dict) or not isinstance(uri := ref.get("uri"), str):
                raise ValueError(f"ARTIFACT_MISSING: {kind}")
            checksum = ref.get("sha256")
            if not checksum and isinstance(ref.get("checksum"), dict):
                checksum = ref["checksum"].get("value")
            if not isinstance(checksum, str) or not self.artifact_store.exists(uri):
                raise ValueError(f"ARTIFACT_MISSING: {kind} requires a stored checksum and file")
            artifacts.append(ArtifactInput(kind=kind, uri=uri, sha256=checksum))
        manifest = RawRecordManifest.model_validate(raw)
        digest = mapping_digest if mapping_digest is not None else version.default_mapping_digest
        mapping = MappingRevisionService(self.session).get(version.id, digest) if digest else None
        if digest and mapping is None:
            raise ValueError("MAPPING_MISSING: Selected MappingRevision does not exist")
        selected = {key: InputSelector.model_validate(value) for key, value in (selectors or {}).items()}
        contract = resolve_consistency_representations(calculator, contract, selected, mapping)
        fields = {
            field["name"]: set(field["data_type"].split("|")) - {"null"}
            for field in refs.get("raw_file", {}).get("fields", [])
        }
        for slot in contract.slots:
            selector = selected.get(slot.name)
            if slot.selectable and slot.required and selector is None:
                raise ValueError(f"FIELD_NOT_SELECTED: {slot.name}")
            if slot.required and mapping is None and (
                not slot.selectable or (selector and selector.semantic is not None)
            ):
                raise ValueError(f"MAPPING_MISSING: {slot.name}")
        try:
            frozen = self._record_resolver().preflight(
                manifest, mapping, contract, artifacts, selectors=selected,
            )
        except ValueError as exc:
            code = "MAPPING_MISSING" if str(exc).startswith("unbound") else "INPUT_INCOMPATIBLE"
            raise ValueError(f"{code}: {exc}") from exc
        for entry in frozen.slots:
            binding, slot = entry.binding, entry.slot
            if binding is None:
                continue
            field = binding.field_tokens[0]
            if fields and field not in fields:
                raise ValueError(f"FIELD_NOT_FOUND: {slot.name} → {field}")
            if binding.definition and (
                binding.definition.cardinality != slot.cardinality
                or slot.value_type != "any"
                and binding.definition.type not in {"any", slot.value_type}
            ):
                raise ValueError(f"INPUT_INCOMPATIBLE: {slot.name}")
            if len(binding.field_tokens) != 1 or not fields.get(field):
                continue
            expected = "array" if slot.cardinality == "many" else (
                "string" if binding.transform else slot.value_type
            )
            accepted = {"number", "integer", "float"} if expected == "number" else {expected}
            if expected != "any" and not fields[field].intersection(accepted):
                raise ValueError(f"INPUT_INCOMPATIBLE: {slot.name} expects {expected}, got {field}")
        bound = {entry.slot.name for entry in frozen.slots if entry.binding is not None}
        key = calculator.calculator_key
        if key in {"policy_document_format", "policy_effectiveness"} and "title" not in bound:
            raise ValueError("FIELD_NOT_SELECTED: title")
        if key in {"policy_document_format", "policy_effectiveness", "policy_structure_compliance"}:
            if not bound.intersection({"html", "plain"}):
                raise ValueError("FIELD_NOT_SELECTED: html or plain")
        if key in {"sensitive_dictionary_match", "structured_sensitive_element_regex"}:
            if not bound.intersection({"title", "html", "plain"}):
                raise ValueError("FIELD_NOT_SELECTED: title, html or plain")
        if (key, calculator.version) == ("content_consistency", "2.1.0"):
            if not bound.intersection({"html", "plain"}):
                raise ValueError("FIELD_NOT_SELECTED: html or plain")
        return frozen

    def create(
        self, contract_version_id: UUID, *, mapping_digest: str | None = None
    ) -> EvaluationRunV1:
        contract_row = self.session.get(
            QualityContractVersionModel, contract_version_id
        )
        if contract_row is None:
            raise ValueError("Published Quality Contract Version not found")
        contract = QualityContractVersionV1.model_validate(contract_row.document)
        if contract.status != "PUBLISHED":
            raise ValueError("Only PUBLISHED Quality Contract Versions can run")
        if contract_row.document_version_id:
            if mapping_digest is not None:
                raise ValueError("document runs do not accept a mapping")
            return self._create_document_run(contract_row, contract)
        version = self.session.get(
            DatasetVersionModel, contract_row.target_dataset_version_id
        )
        if version is None or version.status != "PUBLISHED":
            raise ValueError("EvaluationRun requires a PUBLISHED DatasetVersion")
        dataset = self.session.get(DatasetModel, version.dataset_id, with_for_update=True)
        if dataset is not None and not dataset.enabled:
            raise ValueError("数据集已停用，无法创建评测")
        refs = version.artifact_refs or {}
        raw = refs.get("raw_record_manifest")
        if raw is None and "normalized" not in refs:
            raise ValueError("DatasetVersion has no normalized input artifact")
        manifest = RawRecordManifest.model_validate(raw) if raw else None
        logical_plan = LogicalPlannerService(self.session).compile(contract, version)
        logical_by_item = {
            output.contract_item_key: operation
            for operation in logical_plan.operations
            if operation.operation_type == "METRIC"
            for output in operation.output_bindings
        }

        execution_items = [*contract.items, *contract.observation_items]
        prepared: dict[str, dict[str, Any]] = {}
        for item in execution_items:
            logical_operation = logical_by_item[item.key]
            attributes = item.calculation.implementation_constraints.attributes
            calculator = self._calculator(
                str(attributes.get("calculator_key", "")),
                str(attributes.get("calculator_version", "")),
            )
            parameters = self._resolved_parameters(
                calculator, item.calculation.parameters
            )
            self._validate_parameter_schema(calculator, parameters)
            # Fail before persisting a Plan/Run when Registry metadata cannot
            # load the implementation currently deployed with this process.
            self.calculator_loader.load(calculator)
            logical_input = calculator.input_contract.logical
            frozen_input = None
            if manifest is not None:
                frozen_input = self.preflight_inputs(
                    version, calculator,
                    mapping_digest=attributes.get("mapping_digest") or mapping_digest,
                    selectors=attributes.get("input_selectors"),
                    parameters=parameters,
                )
            elif logical_input is not None:
                raise ValueError("Logical Calculator requires a v3 record Manifest")
            prepared[item.key] = {
                "frozen_input": frozen_input,
                "item": item,
                "operation": logical_operation,
                "calculator": calculator,
                "parameters": parameters,
            }

        snapshot_ref = None
        if any(
            "PROFILE_SNAPSHOT" in entry["calculator"].input_artifact_requirements
            for entry in prepared.values()
        ):
            rule_refs: set[str] = set()
            bindings: list[dict[str, Any]] = []
            for entry in prepared.values():
                parameters = entry["parameters"]
                if isinstance(parameters.get("rule_set_ref"), str):
                    rule_refs.add(parameters["rule_set_ref"])
                rule_refs.update(
                    str(value) for value in parameters.get("rule_set_refs", [])
                )
                bindings.append(
                    {
                        "metric": entry["item"].metric.model_dump(mode="json"),
                        "calculator": {
                            "key": entry["calculator"].calculator_key,
                            "version": entry["calculator"].version,
                        },
                        "parameter_fingerprint": _fingerprint(parameters),
                    }
                )
            _, snapshot_ref = ProfileSnapshotService(
                self.session, self.artifact_store
            ).create_or_reuse(
                version.id,
                rule_set_refs=sorted(rule_refs),
                calculator_bindings=bindings,
            )

        grouped_items: set[str] = set()
        stages: list[dict[str, Any]] = []
        group_by_operation = {
            group.operation_id: group
            for group in logical_plan.shared_computation_groups
        }
        for operation in logical_plan.operations:
            group = group_by_operation.get(operation.operation_id)
            if group is None:
                continue
            bindings = []
            for output in operation.output_bindings:
                entry = prepared[output.contract_item_key]
                grouped_items.add(output.contract_item_key)
                bindings.append(self._physical_output_binding(entry))
            first = bindings[0]
            stages.append(
                {
                    "stage_id": f"shared-inference-{group.shared_computation_id}",
                    "order": len(stages),
                    "stage_kind": "SHARED_INFERENCE",
                    "shared_computation_id": group.shared_computation_id,
                    "metric": first["metric"],
                    "calculator": {
                        "key": "llm_semantic_judge",
                        "version": "1.0.0",
                        "operator_type": "llm_semantic_judge",
                    },
                    "resolved_parameters": self._shared_parameters(bindings),
                    "input_schema_ref": first["input_schema_ref"],
                    "output_schema_ref": "qa-eval://schemas/inference/shared-semantic/1.0",
                    "depends_on": [],
                    "logical_operation_ids": [
                        operation.operation_id,
                        *group.member_operation_ids,
                    ],
                    "output_bindings": bindings,
                }
            )
        native_stages: dict[str, dict[str, Any]] = {}
        for item in execution_items:
            if item.key in grouped_items:
                continue
            entry = prepared[item.key]
            calculator = entry["calculator"]
            if calculator.execution.mode == "SHARED_INFERENCE":
                binding = self._physical_output_binding(entry)
                shared_id = f"single-{entry['operation'].operation_id}"
                safety = calculator.calculator_key == "content_safety_compliance"
                stages.append(
                    {
                        "stage_id": f"{'safety' if safety else 'shared'}-inference-{shared_id}",
                        "order": len(stages),
                        "stage_kind": "SAFETY_INFERENCE"
                        if safety
                        else "SHARED_INFERENCE",
                        "shared_computation_id": shared_id,
                        "metric": binding["metric"],
                        "calculator": {
                            "key": "llm_semantic_judge",
                            "version": "1.0.0",
                            "operator_type": "llm_semantic_judge",
                        },
                        "resolved_parameters": self._shared_parameters([binding]),
                        "input_schema_ref": binding["input_schema_ref"],
                        "output_schema_ref": "qa-eval://schemas/inference/shared-semantic/1.0",
                        "logical_operation_ids": [entry["operation"].operation_id],
                        "output_bindings": [binding],
                    }
                )
                continue
            stage = {
                "stage_id": f"metric-{len(stages)}-{item.metric.key}",
                "order": len(stages),
                "metric": {"key": item.metric.key, "version": item.metric.version},
                "calculator": self._calculator_identity(calculator) | {
                    field: item.calculation.implementation_constraints.attributes[field]
                    for field in ("operator_slot", "execution_grain")
                    if field in item.calculation.implementation_constraints.attributes
                },
                "stage_kind": (
                    "AGGREGATE"
                    if calculator.calculator_key == "content_cleanliness"
                    else "DIRECT"
                ),
                "resolved_parameters": entry["parameters"],
                "input_schema_ref": calculator.input_contract.sample_record_schema,
                "output_schema_ref": calculator.output_schema_ref,
                "depends_on": [],
                "logical_operation_ids": [entry["operation"].operation_id],
            }
            if frozen := entry["frozen_input"]:
                stage["calculator"]["frozen_input"] = frozen.model_dump(mode="json")
                stage["calculator"]["record_manifest"] = manifest.model_dump(mode="json")
            if calculator.native_output is not None:
                binding = entry["operation"].output_bindings[0].model_dump(mode="json")
                binding["native_output"] = calculator.native_output.model_dump(
                    mode="json"
                )
                stage["output_bindings"] = [binding]
                # Identical deterministic invocations can share execution while
                # each contract item retains an independently addressable result.
                identity = _fingerprint(
                    {
                        key: stage[key]
                        for key in (
                            "metric",
                            "calculator",
                            "resolved_parameters",
                            "input_schema_ref",
                        )
                    }
                )
                if calculator.deterministic and identity in native_stages:
                    shared = native_stages[identity]
                    shared["output_bindings"].append(binding)
                    shared["logical_operation_ids"].extend(
                        stage["logical_operation_ids"]
                    )
                    continue
                native_stages[identity] = stage
            stages.append(stage)
        child_stage_ids = [
            stage["stage_id"]
            for stage in stages
            if str(stage["metric"]["key"]).startswith("020801")
            and not self._native_bindings(stage.get("output_bindings", []))
        ]
        aggregate_stages = [
            stage for stage in stages if stage.get("stage_kind") == "AGGREGATE"
        ]
        non_aggregate_stages = [
            stage for stage in stages if stage.get("stage_kind") != "AGGREGATE"
        ]
        stages = [*non_aggregate_stages, *aggregate_stages]
        for order, stage in enumerate(stages):
            stage["order"] = order
            if stage.get("stage_kind") == "AGGREGATE":
                stage["depends_on"] = child_stage_ids
        now = datetime.now(UTC)
        plan_id = uuid7()
        semantic = {
            "dataset_version_id": str(version.id),
            "contract_version_id": str(contract.contract_version_id),
            "stages": stages,
        }
        plan = PhysicalEvaluationPlanV1.model_validate(
            {
                "physical_plan_id": plan_id,
                "logical_plan_id": logical_plan.logical_plan_id,
                "dataset_version_id": version.id,
                "contract_version_id": contract.contract_version_id,
                "stages": stages,
                "semantic_fingerprint": _fingerprint(semantic),
                "created_at": now,
            }
        )
        run = EvaluationRunV1.model_validate(
            {
                "run_id": uuid7(),
                "physical_plan_id": plan_id,
                "dataset_version_id": version.id,
                "contract_version_id": contract.contract_version_id,
                "status": "PENDING",
                "input_fingerprint": (
                    f"sha256:{snapshot_ref.content_sha256}"
                    if snapshot_ref is not None
                    else _fingerprint(
                        {
                            "content_fingerprint": version.content_fingerprint,
                            "normalized": version.artifact_refs.get("normalized"),
                            **({"frozen_inputs": [
                                entry["frozen_input"].fingerprint()
                                for entry in prepared.values()
                            ]} if manifest else {}),
                            "manifest": version.artifact_refs.get("manifest"),
                        }
                    )
                ),
                "created_at": now,
                "execution": (
                    {
                        "input_artifacts": {
                            "PROFILE_SNAPSHOT": snapshot_ref.model_dump(mode="json")
                        }
                    }
                    if snapshot_ref is not None
                    else {"legacy_input_snapshot": "UNAVAILABLE"}
                ),
            }
        )
        if not contract.items and all(item.strategy == "OBSERVE_ONLY" for item in contract.observation_items):
            run = run.model_copy(
                update={"execution": {**run.execution, "native_observation_only": True}}
            )
        self.session.add(
            LogicalEvaluationPlanModel(
                logical_plan_id=logical_plan.logical_plan_id,
                dataset_version_id=version.id,
                contract_version_id=contract.contract_version_id,
                semantic_fingerprint=logical_plan.semantic_fingerprint,
                document=logical_plan.model_dump(mode="json"),
            )
        )
        self.session.flush()
        self.session.add(
            PhysicalEvaluationPlanModel(
                physical_plan_id=plan_id,
                logical_plan_id=logical_plan.logical_plan_id,
                dataset_version_id=version.id,
                contract_version_id=contract.contract_version_id,
                semantic_fingerprint=plan.semantic_fingerprint,
                document=plan.model_dump(mode="json"),
            )
        )
        # No ORM relationship links EvaluationRunModel to PhysicalEvaluationPlanModel,
        # so the unit of work orders their INSERTs by mapper class name and would emit
        # evaluation_runs first, violating the FK. Flush the plan row before the run
        # references it.
        self.session.flush()
        self.session.add(
            EvaluationRunModel(
                run_id=run.run_id,
                physical_plan_id=plan_id,
                dataset_version_id=version.id,
                contract_version_id=contract.contract_version_id,
                status=run.status,
                document=run.model_dump(mode="json"),
            )
        )
        self.session.flush()
        return run

    def _create_document_run(self, row, contract):
        version = self.session.get(DocumentVersionModel, row.document_version_id)
        frozen = contract.document_target.model_dump(mode="json") if contract.document_target else None
        if version is None or frozen is None or any(
            frozen.get(key) != value for key, value in document_target(version).items()
        ):
            raise ValueError("DocumentVersion does not match published contract")
        dataset = self.session.get(DatasetModel, version.dataset_id, with_for_update=True)
        if dataset is None or not dataset.enabled:
            raise ValueError("数据集已停用，无法创建评测")
        descriptors = []
        stages = []
        for order, item in enumerate(contract.items):
            config = item.calculation.implementation_constraints.attributes
            descriptor = self._calculator(config["calculator_key"], config["calculator_version"])
            if descriptor.code_digest != config["code_digest"]:
                raise ValueError("published Calculator digest changed")
            implementation = self.calculator_loader.load(descriptor)
            params = item.calculation.parameters
            self._validate_parameter_schema(descriptor, params)
            if implementation.configuration(params)[1].digest() != config["configuration_sha256"]:
                raise ValueError("published configuration digest changed")
            descriptors.append(descriptor)
            stages.append(PhysicalStageV1(
                stage_id=item.key, order=order,
                metric={"key": item.metric.key, "version": item.metric.version},
                calculator={**self._calculator_identity(descriptor),
                            "configuration_sha256": config["configuration_sha256"]},
                resolved_parameters=params, input_schema_ref="DOCUMENT",
                output_schema_ref=descriptor.output_schema_ref,
                logical_operation_ids=[item.key],
            ))
        logical = LogicalPlannerService(self.session).compile_document(contract, version, descriptors)
        target = {"document_version_id": version.id, "contract_version_id": row.contract_version_id}
        now = datetime.now(UTC)
        plan = PhysicalEvaluationPlanV1(
            **target, physical_plan_id=uuid7(), logical_plan_id=logical.logical_plan_id,
            stages=stages, created_at=now, document_input=frozen,
            semantic_fingerprint=_fingerprint({"document": frozen, "stages": [x.model_dump(mode="json") for x in stages]}),
        )
        run = EvaluationRunV1(
            **target, run_id=uuid7(), physical_plan_id=plan.physical_plan_id,
            status="PENDING", input_fingerprint="sha256:" + frozen["sha256"],
            created_at=now, execution={"document": frozen},
        )
        self.session.add(LogicalEvaluationPlanModel(
            **target, logical_plan_id=logical.logical_plan_id,
            semantic_fingerprint=logical.semantic_fingerprint, document=logical.model_dump(mode="json"),
        ))
        self.session.flush()
        self.session.add(PhysicalEvaluationPlanModel(
            **target, physical_plan_id=plan.physical_plan_id, logical_plan_id=logical.logical_plan_id,
            semantic_fingerprint=plan.semantic_fingerprint, document=plan.model_dump(mode="json"),
        ))
        self.session.flush()
        self.session.add(EvaluationRunModel(
            **target, run_id=run.run_id, physical_plan_id=plan.physical_plan_id,
            status=run.status, document=run.model_dump(mode="json"),
        ))
        self.session.flush()
        return run

    def get_physical_plan(self, physical_plan_id: UUID) -> PhysicalEvaluationPlanV1 | None:
        row = self.session.get(PhysicalEvaluationPlanModel, physical_plan_id)
        return None if row is None else PhysicalEvaluationPlanV1.model_validate(row.document)

    def get_logical_plan(self, logical_plan_id: UUID) -> LogicalEvaluationPlanV1 | None:
        row = self.session.get(LogicalEvaluationPlanModel, logical_plan_id)
        return (
            None
            if row is None
            else LogicalEvaluationPlanV1.model_validate(row.document)
        )

    def logical_plan_for_contract(
        self, contract_version_id: UUID
    ) -> LogicalEvaluationPlanV1 | None:
        row = self.session.scalar(
            select(LogicalEvaluationPlanModel)
            .where(
                LogicalEvaluationPlanModel.contract_version_id == contract_version_id
            )
            .order_by(LogicalEvaluationPlanModel.created_at.desc())
        )
        return (
            None
            if row is None
            else LogicalEvaluationPlanV1.model_validate(row.document)
        )

    def execute(self, run_id: UUID, *, worker_id: str | None = None) -> EvaluationRunV1:
        row = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if row is None:
            raise ValueError("EvaluationRun not found")
        run = EvaluationRunV1.model_validate(row.document)
        if run.status != "PENDING":
            raise ValueError("Only PENDING EvaluationRuns can start")
        plan_row = self.session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        version = (
            self.session.get(DocumentVersionModel, run.document_version_id)
            if run.document_version_id else self.session.get(DatasetVersionModel, run.dataset_version_id)
        )
        if plan_row is None or version is None:
            raise ValueError("EvaluationRun references missing execution inputs")
        plan = PhysicalEvaluationPlanV1.model_validate(plan_row.document)
        if (run.dataset_version_id, run.document_version_id, run.contract_version_id) != (
            plan.dataset_version_id, plan.document_version_id, plan.contract_version_id
        ):
            raise ValueError("Run and plan evaluation targets differ")
        if run.document_version_id and plan.document_input != run.execution.get("document"):
            raise ValueError("Run and plan frozen document inputs differ")
        run = run.model_copy(
            update={
                "status": "RUNNING",
                "started_at": datetime.now(UTC),
                "execution": {
                    **run.execution,
                    "worker_id": worker_id,
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                }
                if worker_id
                else run.execution,
            }
        )
        self._save_run(row, run)
        self.session.commit()

        failures = 0
        fatal_error: dict[str, Any] | None = None
        failure_details: list[dict[str, Any]] = []
        document_cache = {}
        completed_stages = set()
        for stage in sorted(plan.stages, key=lambda item: item.order):
            if worker_id:
                run = self._heartbeat_run(row, run, worker_id)
                if run.status in _TERMINAL_RUN_STATUSES:
                    return run
            succeeded = self._execute_stage(run, stage, version, plan.semantic_fingerprint, document_cache=document_cache)
            completed_stages.add(stage.stage_id)
            if not succeeded:
                failures += 1
                failed_stage = self.session.scalar(
                    select(StageRunModel)
                    .where(
                        StageRunModel.run_id == run.run_id,
                        StageRunModel.stage_id == stage.stage_id,
                    )
                    .order_by(StageRunModel.attempt.desc())
                )
                stage_error = (
                    (failed_stage.document.get("error") or {}) if failed_stage else {}
                )
                affected_metrics = (
                    [
                        item.get("metric", {}).get("key")
                        for item in stage.output_bindings
                    ]
                    if stage.output_bindings
                    else [stage.metric.key]
                )
                failure_details.append(
                    {
                        "stage_id": stage.stage_id,
                        "affected_metrics": affected_metrics,
                        **stage_error,
                    }
                )
                if stage_error.get("error_category") == "CONTENT_FILTER_REJECTED":
                    fatal_error = {
                        **stage_error,
                        "failed_stage_id": stage.stage_id,
                        "affected_metrics": affected_metrics,
                    }
                    break

        finished = datetime.now(UTC)
        status = (
            "FAILED"
            if fatal_error is not None or failures == len(plan.stages)
            else "PARTIAL_SUCCEEDED"
            if failures
            else "SUCCEEDED"
        )
        self.session.refresh(row, attribute_names=["status", "document", "finished_at"])
        persisted = EvaluationRunV1.model_validate(row.document)
        if persisted.status in _TERMINAL_RUN_STATUSES:
            logger.info(
                "Evaluation Run terminal transition ignored: run_id=%s "
                "previous_status=%s next_status=%s worker_id=%s "
                "transition_reason=late_worker_completion persistence_result=ignored",
                run.run_id,
                persisted.status,
                status,
                worker_id,
            )
            return persisted
        aggregate_error: dict[str, Any] | None = None
        if failure_details:
            aggregate_error = {
                "failed_stage_count": failures,
                "failed_stages": failure_details,
            }
            if failures == len(plan.stages):
                first_failure = failure_details[0]
                aggregate_error.update(
                    {
                        "failed_stage_id": first_failure["stage_id"],
                        "affected_metrics": first_failure["affected_metrics"],
                        "error_category": first_failure.get(
                            "error_category", first_failure.get("type")
                        ),
                        "message": first_failure.get("message"),
                    }
                )
        selected_attempts = {}
        for attempt in self.session.scalars(
            select(StageRunModel).where(
                StageRunModel.run_id == run.run_id,
                StageRunModel.stage_id.in_(completed_stages),
            ).order_by(StageRunModel.attempt.desc())
        ):
            selected_attempts.setdefault(attempt.stage_id, str(attempt.stage_run_id))
        run = run.model_copy(
            update={
                "status": status,
                "finished_at": finished,
                "error": fatal_error or aggregate_error,
                "execution": {**persisted.execution, "selected_attempts": selected_attempts},
            }
        )
        self._save_run(row, run)
        self.session.commit()
        logger.info(
            "Evaluation Run transitioned: run_id=%s previous_status=RUNNING "
            "next_status=%s worker_id=%s transition_reason=stages_converged "
            "persistence_result=committed",
            run.run_id,
            run.status,
            worker_id,
        )
        if run.execution.get("native_observation_only"):
            return run
        try:
            QualityResultService(self.session, self.artifact_store).finalize(run.run_id)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            row = self.session.get(EvaluationRunModel, run.run_id)
            run = run.model_copy(
                update={
                    "status": "PARTIAL_SUCCEEDED"
                    if run.status == "SUCCEEDED"
                    else run.status,
                    "error": {
                        **(run.error or {}),
                        "phase": "QUALITY_RESULT_FINALIZATION",
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            self._save_run(row, run)
            self.session.commit()
        return run

    @staticmethod
    def _native_bindings(bindings):
        return [
            slot for slot in bindings if slot.get("output_kind") == "CALCULATOR_RESULT"
        ]

    def _execute_stage(
        self, run, stage, version, plan_fingerprint=None, *, document_cache=None
    ) -> bool:
        native_bindings = self._native_bindings(stage.output_bindings)
        attempt = 1
        if native_bindings:
            if plan_fingerprint is None:
                plan_fingerprint = self.session.get(
                    PhysicalEvaluationPlanModel, run.physical_plan_id
                ).semantic_fingerprint
            # Serialize attempt allocation against the existing Run row.
            self.session.get(EvaluationRunModel, run.run_id, with_for_update=True)
            latest = self.session.scalar(
                select(StageRunModel)
                .filter_by(run_id=run.run_id, stage_id=stage.stage_id)
                .order_by(StageRunModel.attempt.desc())
                .limit(1)
            )
            if latest is not None:
                if latest.status in {"RUNNING", "SUCCEEDED"}:
                    return latest.status == "SUCCEEDED"
                attempt = latest.attempt + 1
        now = datetime.now(UTC)
        stage_run_id = uuid7()
        params_fingerprint = _fingerprint(stage.resolved_parameters)
        stage_run = StageRunV1.model_validate(
            {
                "stage_run_id": stage_run_id,
                "run_id": run.run_id,
                "physical_plan_id": run.physical_plan_id,
                "stage_id": stage.stage_id,
                "attempt": attempt,
                "status": "RUNNING",
                "calculator": stage.calculator,
                "resolved_parameters": stage.resolved_parameters,
                "parameters_fingerprint": params_fingerprint,
                "input_fingerprint": run.input_fingerprint,
                "started_at": now,
            }
        )
        stage_row = StageRunModel(
            stage_run_id=stage_run_id,
            run_id=run.run_id,
            physical_plan_id=run.physical_plan_id,
            stage_id=stage.stage_id,
            attempt=attempt,
            status="RUNNING",
            document=stage_run.model_dump(mode="json"),
        )
        self.session.add(stage_row)
        self.session.commit()
        try:
            if stage.stage_kind in {"SHARED_INFERENCE", "SAFETY_INFERENCE"}:
                results, output_artifacts, diagnostics, execution_stats = (
                    self._calculate_shared(run, stage_run, stage, version)
                )
            else:
                results, output_artifacts, diagnostics = self._calculate(
                    run, stage_run, stage, version, plan_fingerprint, document_cache=document_cache
                )
                execution_stats = (
                    {"calculator_result_count": len(native_bindings)}
                    if native_bindings
                    else {}
                )
            for result in results:
                self._persist_metric_result(result)
                if run.document_version_id:
                    self.session.flush()
                    self._document_issues(run, stage, result)
            self.session.refresh(
                row := self.session.get(EvaluationRunModel, run.run_id)
            )
            persisted_run = EvaluationRunV1.model_validate(row.document)
            if persisted_run.status in _TERMINAL_RUN_STATUSES:
                self.session.rollback()
                stage_row = self.session.get(
                    StageRunModel, stage_run_id, with_for_update=True
                )
                current_stage = StageRunV1.model_validate(stage_row.document)
                if current_stage.status == "RUNNING":
                    current_stage = current_stage.model_copy(
                        update={
                            "status": "FAILED",
                            "finished_at": datetime.now(UTC),
                            "error": {
                                "code": "RUN_ALREADY_TERMINAL",
                                "message": "Run reached a terminal state before Stage completion",
                            },
                        }
                    )
                    stage_row.status = "FAILED"
                    stage_row.document = current_stage.model_dump(mode="json")
                    self._persist_native_failure(
                        run, current_stage, stage, plan_fingerprint
                    )
                    self.session.commit()
                return False
            stage_run = stage_run.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "finished_at": datetime.now(UTC),
                    "output_artifacts": output_artifacts,
                    "stats": {
                        "metric_result_count": len(results),
                        "observation_count": sum(
                            len(item.observations) for item in results
                        ),
                        "diagnostics": diagnostics,
                        **execution_stats,
                    },
                }
            )
            stage_row.status = "SUCCEEDED"
            stage_row.document = stage_run.model_dump(mode="json")
            self.session.commit()
            return True
        # The stage boundary must persist arbitrary Calculator failures as facts.
        except Exception as exc:
            # A flush/commit failure leaves the Session unusable until rollback.
            # The RUNNING StageRun was committed before execution, so reload it
            # and persist FAILED in a clean transaction first.
            self.session.rollback()
            stage_row = self.session.get(
                StageRunModel, stage_run_id, with_for_update=True
            )
            if stage_row is None:
                raise RuntimeError(
                    "StageRun disappeared while recording failure"
                ) from exc
            metrics = (
                [item.get("metric", {}) for item in stage.output_bindings]
                if stage.stage_kind in {"SHARED_INFERENCE", "SAFETY_INFERENCE"}
                else [stage.metric.model_dump(mode="json")]
            )
            error = {"type": type(exc).__name__, "message": str(exc)}
            llm_metadata = self._find_llm_error_metadata(exc)
            if llm_metadata is not None:
                error.update(llm_metadata)
            if isinstance(exc, (CalculatorError, InferenceOperatorError)):
                error.update({"code": exc.code, "details": exc.details})
            stage_run = stage_run.model_copy(
                update={
                    "status": "FAILED",
                    "finished_at": datetime.now(UTC),
                    "error": error,
                }
            )
            stage_row.status = "FAILED"
            stage_row.document = stage_run.model_dump(mode="json")
            if native_bindings:
                self._persist_native_failure(run, stage_run, stage, plan_fingerprint)
                self.session.commit()
                return False
            self.session.commit()
            for metric in metrics:
                error_result = MetricResultV1.model_validate(
                    {
                        "metric_result_id": uuid7(),
                        "run_id": run.run_id,
                        "stage_run_id": stage_run_id,
                        "dataset_version_id": run.dataset_version_id,
                        "document_version_id": run.document_version_id,
                        "metric": metric,
                        "subject": {
                            "scope": "DOCUMENT" if run.document_version_id else "DATASET",
                            "id": str(run.document_version_id or run.dataset_version_id),
                        },
                        "status": "ERROR",
                        **({"status_detail": {"calculator": stage.calculator}}
                           if "operator_slot" in stage.calculator else {}),
                        "created_at": datetime.now(UTC),
                    }
                )
                self._persist_metric_result(error_result)
                if run.document_version_id:
                    self._persist_document_calculation(run, stage_run, stage, plan_fingerprint, error_result)
            self.session.commit()
            return False

    def fail_running_run(self, run_id: UUID, exc: Exception) -> EvaluationRunV1 | None:
        """Best-effort crash boundary used by background workers.

        A fresh transaction is used so a failed execution transaction cannot roll
        back the terminal status. Existing terminal states are never overwritten.
        """

        self.session.rollback()
        row = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if row is None:
            return None
        run = EvaluationRunV1.model_validate(row.document)
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        error: dict[str, Any] = {
            "phase": "BACKGROUND_EXECUTION",
            "type": type(exc).__name__,
            "message": "Evaluation Run execution failed unexpectedly",
        }
        llm_metadata = self._find_llm_error_metadata(exc)
        if llm_metadata is not None:
            error.update(llm_metadata)
        failed = run.model_copy(
            update={
                "status": "FAILED",
                "finished_at": datetime.now(UTC),
                "error": error,
            }
        )
        self._save_run(row, failed)
        self.session.commit()
        logger.error(
            "Evaluation Run transitioned: run_id=%s previous_status=%s "
            "next_status=FAILED worker_id=%s error_category=%s "
            "transition_reason=background_execution_crash persistence_result=committed",
            run_id,
            run.status,
            run.execution.get("worker_id"),
            error.get("error_category"),
        )
        return failed

    def claim_pending_run(self, run_id: UUID, *, worker_id: str) -> EvaluationRunV1:
        """Persist queue ownership before submitting an in-process Future."""

        row = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if row is None:
            raise ValueError("EvaluationRun not found")
        run = EvaluationRunV1.model_validate(row.document)
        if run.status != "PENDING":
            return run
        claimed = run.model_copy(
            update={
                "execution": {
                    **run.execution,
                    "worker_id": worker_id,
                    "claimed_at": datetime.now(UTC).isoformat(),
                }
            }
        )
        self._save_run(row, claimed)
        self.session.commit()
        return claimed

    def sweep_interrupted(self, *, current_worker_id: str) -> int:
        """Fail Runs owned by a previous in-process worker after process restart.

        Unowned legacy Runs are deliberately ignored: without an ownership fact we
        cannot distinguish an orphan from a live task in another process.
        """

        rows = self.session.scalars(
            select(EvaluationRunModel).where(
                EvaluationRunModel.status.in_(["PENDING", "RUNNING"])
            )
        ).all()
        swept = 0
        transitions: list[tuple[UUID, str, str]] = []
        for row in rows:
            run = EvaluationRunV1.model_validate(row.document)
            previous_worker = run.execution.get("worker_id")
            if not previous_worker or previous_worker == current_worker_id:
                continue
            finished_at = datetime.now(UTC)
            interrupted_stages: list[str] = []
            stage_rows = self.session.scalars(
                select(StageRunModel).where(
                    StageRunModel.run_id == run.run_id,
                    StageRunModel.status == "RUNNING",
                )
            ).all()
            plan_row = self.session.get(
                PhysicalEvaluationPlanModel, run.physical_plan_id
            )
            physical_stages = (
                {
                    stage["stage_id"]: PhysicalStageV1.model_validate(stage)
                    for stage in plan_row.document.get("stages", [])
                    if self._native_bindings(stage.get("output_bindings", []))
                }
                if plan_row is not None
                else {}
            )
            for stage_row in stage_rows:
                stage = StageRunV1.model_validate(stage_row.document).model_copy(
                    update={
                        "status": "FAILED",
                        "finished_at": finished_at,
                        "error": {
                            "code": "WORKER_INTERRUPTED",
                            "error_category": "WORKER_INTERRUPTED",
                            "retryable": False,
                            "message": "Execution worker exited before Stage completion",
                        },
                    }
                )
                stage_row.status = "FAILED"
                stage_row.document = stage.model_dump(mode="json")
                if execution_stage := physical_stages.get(stage.stage_id):
                    self._persist_native_failure(
                        run, stage, execution_stage, plan_row.semantic_fingerprint
                    )
                interrupted_stages.append(stage.stage_id)
            failed = run.model_copy(
                update={
                    "status": "FAILED",
                    "finished_at": finished_at,
                    "error": {
                        "phase": "STARTUP_SWEEP",
                        "error_category": "WORKER_INTERRUPTED",
                        "retryable": False,
                        "message": "Execution worker exited before Run completion",
                        "interrupted_stage_ids": interrupted_stages,
                        "previous_worker_id": previous_worker,
                    },
                }
            )
            self._save_run(row, failed)
            swept += 1
            transitions.append((run.run_id, run.status, str(previous_worker)))
        if swept:
            self.session.commit()
        for run_id, previous_status, previous_worker in transitions:
            logger.error(
                "Evaluation Run transitioned: run_id=%s previous_status=%s "
                "next_status=FAILED worker_id=%s error_category=WORKER_INTERRUPTED "
                "transition_reason=startup_sweep persistence_result=committed",
                run_id,
                previous_status,
                previous_worker,
            )
        return swept

    def _heartbeat_run(
        self, row: EvaluationRunModel, run: EvaluationRunV1, worker_id: str
    ) -> EvaluationRunV1:
        self.session.refresh(row, attribute_names=["status", "document"])
        persisted = EvaluationRunV1.model_validate(row.document)
        if persisted.status in _TERMINAL_RUN_STATUSES:
            return persisted
        if persisted.execution.get("worker_id") != worker_id:
            raise RuntimeError("Evaluation Run execution ownership was lost")
        updated = persisted.model_copy(
            update={
                "execution": {
                    **persisted.execution,
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                }
            }
        )
        self._save_run(row, updated)
        self.session.commit()
        return updated

    @staticmethod
    def _find_llm_error_metadata(exc: BaseException) -> dict[str, Any] | None:
        """Preserve structured Provider metadata through wrapper exceptions."""

        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            metadata = getattr(current, "metadata", None)
            if callable(metadata):
                value = metadata()
                if isinstance(value, dict) and "error_category" in value:
                    return value
            current = current.__cause__ or current.__context__
        return None

    def _persist_metric_result(self, result: MetricResultV1) -> None:
        self.session.add(
            MetricResultModel(
                metric_result_id=result.metric_result_id,
                run_id=result.run_id,
                stage_run_id=result.stage_run_id,
                dataset_version_id=result.dataset_version_id,
                document_version_id=result.document_version_id,
                metric_key=result.metric.key,
                metric_version=result.metric.version,
                status=result.status,
                document=result.model_dump(mode="json"),
            )
        )

    def _calculate_shared(self, run, stage_run, stage, version):
        planned_model = stage.resolved_parameters.get("model")
        actual_model = getattr(self.llm_client, "model", None)
        if planned_model and actual_model and planned_model != actual_model:
            raise InferenceOperatorError(
                "MODEL_CONFIGURATION_MISMATCH",
                "The configured Provider model differs from the Physical Plan",
                planned_model=planned_model,
                actual_model=actual_model,
            )
        input_kinds = {"NORMALIZED_DATA"}
        for binding in stage.output_bindings:
            calculator_info = binding["calculator"]
            shared_descriptor = self._calculator(
                str(calculator_info["key"]), str(calculator_info["version"])
            )
            input_kinds.update(shared_descriptor.input_artifact_requirements)
        requirements = [
            {
                "metric": binding["metric"],
                "prompt_family": binding["execution"]["prompt_family"],
                "prompt_version": binding["execution"]["prompt_version"],
                "rubric": binding["execution"]["rubric"],
                "rubric_version": binding["execution"]["rubric_version"],
                "output_schema": binding["execution"]["rubric_output_schema"],
                "output_field": binding["execution"]["output_field"],
                "input_roles": binding["execution"]["input_roles"],
                "static_context": binding["execution"]["static_context"],
                "required_static_context": binding["execution"][
                    "required_static_context"
                ],
                "preflight_status": self._shared_binding_preflight(binding, version),
            }
            for binding in stage.output_bindings
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_artifacts = self._materialize_inputs(
                run, version, sorted(input_kinds), root
            )
            input_path = input_artifacts["NORMALIZED_DATA"]
            inference = LLMSemanticJudgeOperator(self.llm_client).execute(
                input_path=input_path,
                shared_computation_id=stage.shared_computation_id or stage.stage_id,
                requirements=requirements,
                parameters=stage.resolved_parameters,
            )
            results = []
            output_artifacts: list[dict[str, Any]] = []
            diagnostics: list[dict[str, Any]] = []
            for binding in stage.output_bindings:
                try:
                    calculator_info = binding["calculator"]
                    registered = self._calculator(
                        str(calculator_info["key"]), str(calculator_info["version"])
                    )
                    descriptor = registered.model_copy(
                        update={
                            "entrypoint": calculator_info["entrypoint"],
                            "code_digest": calculator_info["code_digest"],
                        }
                    )
                    calculator = self.calculator_loader.load(descriptor)
                    parameters = binding["parameters"]
                    self._validate_parameter_schema(descriptor, parameters)
                    output_dir = root / f"metric-{binding['metric']['key']}"
                    context = CalculatorContext(
                        run_id=run.run_id,
                        stage_run_id=stage_run.stage_run_id,
                        dataset_version_id=run.dataset_version_id,
                        metric_key=binding["metric"]["key"],
                        metric_version=binding["metric"]["version"],
                        input_path=input_path,
                        output_dir=output_dir,
                        input_schema_ref=binding["input_schema_ref"],
                        input_artifacts=input_artifacts,
                        progress=self._progress_callback(stage_run.stage_run_id),
                    )
                    output = calculator.score_shared(
                        context, parameters, inference.records
                    )
                    evidence, artifacts = self._store_evidence(
                        run.run_id, stage_run.stage_run_id, output.evidence
                    )
                    output_artifacts.extend(artifacts)
                    diagnostics.extend(output.diagnostics)
                    payload = {
                        "status": output.status,
                        "status_detail": output.status_detail,
                        "observations": output.observations,
                        "population": output.population,
                        "semantic_coverage": output.semantic_coverage,
                        "evidence": evidence,
                    }
                except Exception as exc:  # noqa: BLE001 - isolate sibling metrics
                    diagnostics.append(
                        {
                            "code": "METRIC_SCORING_FAILED",
                            "metric_key": binding["metric"]["key"],
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
                    payload = {
                        "status": "ERROR",
                        "status_detail": {
                            "reason_code": "METRIC_SCORING_FAILED",
                            "message": str(exc),
                        },
                    }
                results.append(
                    MetricResultV1.model_validate(
                        {
                            "metric_result_id": uuid7(),
                            "run_id": run.run_id,
                            "stage_run_id": stage_run.stage_run_id,
                            "dataset_version_id": run.dataset_version_id,
                            "metric": binding["metric"],
                            "subject": {
                                "scope": "DATASET",
                                "id": str(run.dataset_version_id),
                            },
                            **payload,
                            "created_at": datetime.now(UTC),
                        }
                    )
                )
            audit_path = root / "shared-inference-audit.json"
            audit_path.write_text(
                json.dumps(
                    {
                        "audit": inference.audit,
                        "stats": inference.stats,
                        "records": inference.records,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            from qa_eval.calculators.protocol import EvidenceOutput

            _, audit_artifacts = self._store_evidence(
                run.run_id,
                stage_run.stage_run_id,
                [
                    EvidenceOutput(
                        path=audit_path,
                        kind="SHARED_INFERENCE_AUDIT",
                        schema_ref="qa-eval://schemas/evidence/shared-inference-audit/1.0",
                        record_count=len(inference.records),
                        error_count=sum(
                            bool(item.get("missing_outputs"))
                            for item in inference.records
                        ),
                        attributes=inference.audit,
                    )
                ],
            )
            output_artifacts.extend(audit_artifacts)
        return results, output_artifacts, diagnostics, inference.stats

    def _calculate(self, run, stage_run, stage, version, plan_fingerprint, *, document_cache=None):
        registered_descriptor = self._calculator(
            str(stage.calculator.get("key", "")),
            str(stage.calculator.get("version", "")),
        )
        descriptor = registered_descriptor.model_copy(
            update={
                "entrypoint": str(stage.calculator.get("entrypoint", "")),
                "code_digest": str(stage.calculator.get("code_digest", "")),
            }
        )
        calculator = self.calculator_loader.load(descriptor)
        self._validate_parameter_schema(descriptor, stage.resolved_parameters)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            resolved_records = None
            parsed_document = None
            if stage.input_schema_ref == "DOCUMENT":
                input_path = root / "document"
                input_artifacts = {}
                parsed_document = self._load_document(run, version, root, document_cache)
            elif stage.input_schema_ref == "3.0":
                frozen = FrozenInputPlan.model_validate(stage.calculator["frozen_input"])
                manifest = RawRecordManifest.model_validate(stage.calculator["record_manifest"])
                if manifest.dataset_version_id != run.dataset_version_id:
                    raise ValueError("frozen record Manifest belongs to another dataset version")
                if frozen.manifest_fingerprint != FrozenManifest(manifest=manifest).fingerprint():
                    raise ValueError("frozen record Manifest fingerprint mismatch")
                input_path = self._worker_input_path(manifest.source_uri, root / "raw-records.jsonl")
                input_artifacts = {}
                for artifact in frozen.artifacts:
                    path = self._worker_input_path(artifact.uri, root / artifact.kind)
                    if _sha256_file(path) != artifact.sha256.removeprefix("sha256:"):
                        raise ValueError(f"Input artifact checksum mismatch: {artifact.kind}")
                    input_artifacts[artifact.kind] = path
                resolver = self._record_resolver()

                def resolved_records():
                    return resolver.iter_records(frozen, read_records(input_path, manifest=manifest))
            else:
                input_artifacts = self._materialize_inputs(
                    run, version, descriptor.input_artifact_requirements, root
                )
                input_path = input_artifacts.get("NORMALIZED_DATA") or next(
                    iter(input_artifacts.values())
                )
            context = CalculatorContext(
                run_id=run.run_id,
                stage_run_id=stage_run.stage_run_id,
                dataset_version_id=run.dataset_version_id,
                metric_key=stage.metric.key,
                metric_version=stage.metric.version,
                input_path=input_path,
                output_dir=output_dir,
                input_schema_ref=stage.input_schema_ref,
                input_artifacts=input_artifacts,
                progress=self._progress_callback(stage_run.stage_run_id),
                run_created_at=run.created_at,
                resolved_records=resolved_records,
                parsed_document=parsed_document,
            )
            if parsed_document is not None:
                context.progress(0, {"phase": "JUDGING", "document_block_count": len(parsed_document.blocks)})
            output = calculator.execute(context, stage.resolved_parameters)
            native_bindings = self._native_bindings(stage.output_bindings)
            if native_bindings:
                if not isinstance(output, NativeCalculatorOutput):
                    raise CalculatorValidationError("expected native Calculator output")
                artifacts = []
                for binding in native_bindings:
                    result, refs = self._store_native_output(
                        run, stage_run, stage, binding, plan_fingerprint, output
                    )
                    self._persist_native_result(result)
                    artifacts.extend(refs)
                return [], artifacts, output.diagnostics
            if isinstance(output, NativeCalculatorOutput):
                raise CalculatorValidationError("expected MetricResult output")
            evidence, output_artifacts = self._store_evidence(
                run.run_id, stage_run.stage_run_id, output.evidence
            )
        result = MetricResultV1.model_validate(
            {
                "metric_result_id": uuid7(),
                "run_id": run.run_id,
                "stage_run_id": stage_run.stage_run_id,
                "dataset_version_id": run.dataset_version_id,
                "document_version_id": run.document_version_id,
                "metric": stage.metric.model_dump(mode="json"),
                "subject": {"scope": "DOCUMENT" if run.document_version_id else "DATASET",
                            "id": str(run.document_version_id or run.dataset_version_id)},
                "status": output.status,
                "status_detail": output.status_detail | (
                    {"calculator": stage.calculator}
                    if "operator_slot" in stage.calculator else {}
                ),
                "observations": output.observations,
                "population": output.population,
                "semantic_coverage": output.semantic_coverage,
                "evidence": evidence,
                "created_at": datetime.now(UTC),
            }
        )
        if run.document_version_id:
            self._persist_document_calculation(run, stage_run, stage, plan_fingerprint, result)
        return [result], output_artifacts, output.diagnostics

    def _load_document(self, run, version, root, cache):
        if cache is None:
            cache = {}
        if "error" in cache:
            raise ValueError(cache["error"])
        if "parsed" not in cache:
            frozen = run.execution["document"]
            try:
                if any(frozen.get(key) != value for key, value in document_target(version).items()):
                    raise ValueError("DocumentVersion differs from frozen input")
                data = self._worker_input_path(frozen["uri"], root / "document").read_bytes()
                if hashlib.sha256(data).hexdigest() != frozen["sha256"]:
                    raise ValueError("Frozen document checksum mismatch")
                cache["parsed"] = parse_document(data, Path(frozen["filename"]).suffix.lower())
            except (ValueError, OSError) as exc:
                cache["error"] = str(exc)
                raise
        return cache["parsed"]

    def _persist_document_calculation(self, run, stage_run, stage, plan_fingerprint, result):
        numerator, denominator = "satisfied_aspect_count", "required_aspect_count"
        definitions = [
            {"key": key, "kind": "count", "unit": "aspect", "population": "ASPECT",
             "method": "Count of documentation aspects"}
            for key in (numerator, denominator)
        ]
        definitions.append({
            "key": "completeness_ratio", "kind": "ratio", "unit": "ratio",
            "population": "ASPECT", "method": "Satisfied / required",
            "numerator_key": numerator, "denominator_key": denominator,
        })
        calculator_result = self._native_result(
            run, stage_run, stage, {
                "contract_item_key": stage.stage_id, "output_slot_id": stage.stage_id,
                "native_output": {"schema_version": "1.0", "statistics": definitions},
            }, plan_fingerprint,
            status=result.status, status_detail=result.status_detail,
            statistics={item.key: item.value for item in result.observations},
            evidence_artifact_refs=result.evidence,
        )
        self._persist_native_result(calculator_result)

    def _document_issues(self, run, stage, result):
        issues = []
        now = datetime.now(UTC)
        for evidence in result.evidence:
            with tempfile.TemporaryDirectory() as folder:
                path = self._worker_input_path(evidence.artifact.uri, Path(folder) / "evidence")
                with path.open(encoding="utf-8") as stream:
                    for number, line in enumerate(stream, 1):
                        row = json.loads(line)
                        if row["outcome"] not in {"MISSING", "INSUFFICIENT"}:
                            continue
                        code = row["item_code"]
                        issues.append(QualityIssueV1(
                            issue_id=uuid7(), run_id=run.run_id, document_version_id=run.document_version_id,
                            source={"metric_result_id": result.metric_result_id, "metric_key": result.metric.key,
                                    "metric_version": result.metric.version, "measurement": "completeness_ratio"},
                            detector={"key": stage.calculator["key"], "version": stage.calculator["version"]},
                            category="DOCUMENTATION", code=row["outcome"], determination="CONFIRMED",
                            severity="WARNING", scope="DOCUMENT",
                            subject={"type": "DOCUMENT", "document_version_id": run.document_version_id},
                            message=row["reason"], evidence=[{"evidence_id": evidence.evidence_id,
                                                           "locator": {"row_id": row["row_id"], "line_number": number, "item_code": code}}],
                            dedup_key=f"{stage.stage_id}:{code}", status="OPEN", created_at=now, updated_at=now,
                        ))
        QualityResultService(self.session, self.artifact_store)._upsert_issues(issues)

    @staticmethod
    def _native_result(run, stage_run, stage, binding, plan_fingerprint, **payload):
        slot = binding["output_slot_id"]
        return CalculatorResultV1(
            calculator_result_id=uuid5(stage_run.stage_run_id, slot),
            run_id=run.run_id,
            stage_run_id=stage_run.stage_run_id,
            dataset_version_id=run.dataset_version_id,
            document_version_id=run.document_version_id,
            contract_item_key=binding["contract_item_key"],
            output_slot_id=slot,
            metric=stage.metric,
            calculator_key=stage.calculator["key"],
            calculator_version=stage.calculator["version"],
            input_fingerprint=run.input_fingerprint,
            plan_fingerprint=plan_fingerprint,
            output_schema=binding["native_output"],
            **payload,
        )

    def _persist_native_failure(self, run, stage_run, stage, plan_fingerprint):
        for binding in self._native_bindings(stage.output_bindings):
            self._persist_native_result(
                self._native_result(
                    run,
                    stage_run,
                    stage,
                    binding,
                    plan_fingerprint,
                    status="ERROR",
                    status_detail=stage_run.error,
                )
            )

    def _store_native_output(
        self, run, stage_run, stage, binding, plan_fingerprint, output
    ):
        result = self._native_result(
            run,
            stage_run,
            stage,
            binding,
            plan_fingerprint,
            status=output.status,
            status_detail=output.status_detail,
            statistics=output.statistics,
        )
        schema: CalculatorOutputSchemaV1 = result.output_schema
        decisions = output.decisions
        if (decisions is None) != (schema.decision_retention == "NONE"):
            raise CalculatorValidationError(
                "decision artifact does not match retention"
            )
        items = ([decisions] if decisions is not None else []) + output.evidence
        artifact_keys = {item.path.name for item in items}
        if len(artifact_keys) != len(items):
            raise CalculatorValidationError("native artifact filenames must be unique")
        if decisions is not None:
            if (
                decisions.schema_ref != DECISION_SCHEMA_REF
                or decisions.path.suffix != ".jsonl"
            ):
                raise CalculatorValidationError(
                    "invalid decision artifact schema or format"
                )
            with decisions.path.open(encoding="utf-8") as rows:
                count = 0
                for line in rows:
                    decision = CalculatorDecisionV1.model_validate_json(line)
                    if any(
                        ref.artifact_key not in artifact_keys
                        for ref in decision.evidence_refs
                    ):
                        raise CalculatorValidationError(
                            "decision references an undeclared evidence artifact"
                        )
                    count += 1
            population = output.statistics[schema.decision_population_key]
            if (
                count != decisions.record_count
                or count > population
                or (schema.decision_retention == "ALL" and count != population)
            ):
                raise CalculatorValidationError(
                    "decision artifact does not match its population"
                )
        evidence, refs = self._store_evidence(
            run.run_id,
            stage_run.stage_run_id,
            items,
            namespace=str(result.calculator_result_id),
            attributes={
                "calculator_result_id": str(result.calculator_result_id),
                "output_schema_version": schema.schema_version,
                "decision_retention": schema.decision_retention,
                "retained_decisions_cover_population": schema.decision_retention
                == "ALL",
            },
        )
        document = result.model_dump(mode="json")
        document["decision_artifact_refs"] = (
            evidence[:1] if decisions is not None else []
        )
        document["evidence_artifact_refs"] = (
            evidence[1:] if decisions is not None else evidence
        )
        return CalculatorResultV1.model_validate(document), refs

    def _persist_native_result(self, result: CalculatorResultV1) -> None:
        key = result.calculator_result_id
        document = result.model_dump(mode="json")
        existing = self.session.get(CalculatorResultModel, key)
        if existing is not None:
            if existing.document != document:
                raise CalculatorValidationError(
                    "Calculator output slot already finalized"
                )
            return
        self.session.add(
            CalculatorResultModel(
                calculator_result_id=key,
                run_id=result.run_id,
                stage_run_id=result.stage_run_id,
                contract_item_key=result.contract_item_key,
                output_slot_id=result.output_slot_id,
                document=document,
            )
        )
        self.session.flush()
        for evidence in [
            *result.decision_artifact_refs,
            *result.evidence_artifact_refs,
        ]:
            self.session.add(
                CalculatorResultArtifactModel(
                    evidence_id=evidence.evidence_id, calculator_result_id=key
                )
            )

    def _worker_input_path(self, uri: str, destination: Path) -> Path:
        local_path = getattr(self.artifact_store, "local_read_path", None)
        if local_path is not None:
            return local_path(uri)
        self.artifact_store.materialize(uri, destination)
        return destination

    def _materialize_inputs(
        self,
        run: EvaluationRunV1,
        version: DatasetVersionModel,
        requirements: list[str],
        root: Path,
    ) -> dict[str, Path]:
        required = requirements or ["NORMALIZED_DATA"]
        if len(required) != len(set(required)):
            raise ValueError("Calculator input_artifact_requirements must be unique")
        version_refs = version.artifact_refs or {}
        destinations = {
            "NORMALIZED_DATA": root / "normalized-data.jsonl",
            "DATASET_MANIFEST": root / "dataset-manifest.json",
            "PROFILE_SNAPSHOT": root / "profile-snapshot.json",
            "DATASET_PROFILE": root / "dataset-profile.json",
        }
        result: dict[str, Path] = {}
        for kind in required:
            if kind not in destinations:
                raise ValueError(f"Unsupported Calculator input artifact: {kind}")
            destination = destinations[kind]
            if kind == "NORMALIZED_DATA":
                ref = version_refs.get("normalized")
            elif kind == "DATASET_MANIFEST":
                ref = version_refs.get("manifest")
            elif kind == "PROFILE_SNAPSHOT":
                ref = (
                    run.execution.get("input_artifacts", {})
                    .get("PROFILE_SNAPSHOT", {})
                    .get("artifact")
                )
            else:
                profile = self.session.scalar(
                    select(DatasetProfileModel)
                    .where(DatasetProfileModel.dataset_version_id == version.id)
                    .order_by(DatasetProfileModel.created_at.desc())
                    .limit(1)
                )
                if profile is None:
                    raise ValueError("DatasetProfile input artifact is unavailable")
                destination.write_bytes(_canonical_json_bytes(profile.document))
                result[kind] = destination
                continue
            if not isinstance(ref, dict) or not ref.get("uri"):
                raise ValueError(f"Required input artifact is unavailable: {kind}")
            self.artifact_store.materialize(str(ref["uri"]), destination)
            declared = ref.get("sha256") or (
                ref.get("checksum", {}).get("value")
                if isinstance(ref.get("checksum"), dict)
                else None
            )
            if declared is not None and str(declared).removeprefix(
                "sha256:"
            ) != _sha256_file(destination):
                raise ValueError(f"Input artifact checksum mismatch: {kind}")
            result[kind] = destination
        return result

    def _progress_callback(self, stage_run_id: UUID):
        def update(processed: int, stats: dict[str, Any]) -> None:
            if processed < 0:
                raise ValueError("processed progress must be nonnegative")
            row = self.session.get(StageRunModel, stage_run_id)
            if row is None:
                return
            stage = StageRunV1.model_validate(row.document)
            if stage.status != "RUNNING":
                return
            progress_stats = {
                **stage.stats,
                **stats,
                "processed": processed,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            updated = stage.model_copy(update={"stats": progress_stats})
            row.document = updated.model_dump(mode="json")
            run_row = self.session.get(EvaluationRunModel, stage.run_id)
            if run_row is not None:
                run = EvaluationRunV1.model_validate(run_row.document)
                if run.status == "RUNNING" and run.execution.get("worker_id"):
                    refreshed = run.model_copy(
                        update={
                            "execution": {
                                **run.execution,
                                "heartbeat_at": datetime.now(UTC).isoformat(),
                            }
                        }
                    )
                    run_row.document = refreshed.model_dump(mode="json")
            self.session.flush()
            self.session.commit()

        return update

    def _store_evidence(
        self, run_id, stage_run_id, items, *, namespace=None, attributes=None
    ):
        evidence = []
        output_artifacts = []
        for item in items:
            filename = item.path.name
            prefix = f"evaluation-runs/{run_id}/{stage_run_id}"
            relative = (
                f"{prefix}/{namespace}/{filename}"
                if namespace
                else f"{prefix}/{filename}"
            )
            stored = self.artifact_store.put_file(relative, item.path)
            artifact = {
                "artifact_id": uuid5(UUID(namespace), relative)
                if namespace
                else uuid7(),
                "kind": "METRIC_EVIDENCE",
                "uri": stored.uri,
                "format": Path(filename).suffix.removeprefix("."),
                "media_type": "application/x-ndjson"
                if filename.endswith("jsonl")
                else "application/json",
                "schema_ref": item.schema_ref,
                "size_bytes": stored.size_bytes,
                "record_count": item.record_count,
                "checksum": {"algorithm": "sha256", "value": stored.sha256},
            }
            output_artifacts.append(artifact)
            evidence.append(
                {
                    "evidence_id": uuid5(UUID(namespace), filename)
                    if namespace
                    else uuid7(),
                    "kind": item.kind,
                    "schema_ref": item.schema_ref,
                    "artifact": artifact,
                    "subject_scope": item.subject_scope,
                    "summary": {
                        "record_count": item.record_count,
                        "error_count": item.error_count,
                        "attributes": (
                            {**item.attributes, "artifact_key": filename, **attributes}
                            if attributes is not None
                            else item.attributes
                        ),
                    },
                    "redaction": {
                        "contains_raw_content": item.contains_raw_content,
                        "policy": item.redaction_policy,
                    },
                }
            )
        return evidence, output_artifacts

    def get(self, run_id: UUID) -> EvaluationRunV1 | None:
        row = self.session.get(EvaluationRunModel, run_id)
        return None if row is None else EvaluationRunV1.model_validate(row.document)

    def list_runs(self) -> list[EvaluationRunV1]:
        rows = self.session.scalars(
            select(EvaluationRunModel).order_by(EvaluationRunModel.created_at.desc())
        ).all()
        return [EvaluationRunV1.model_validate(row.document) for row in rows]

    def list_calculator_results(
        self, run_id: UUID, contract_item_key: str | None = None
    ):
        query = select(CalculatorResultModel).where(
            CalculatorResultModel.run_id == run_id
        )
        if contract_item_key is not None:
            query = query.where(
                CalculatorResultModel.contract_item_key == contract_item_key
            )
        return [
            CalculatorResultV1.model_validate(row.document)
            for row in self.session.scalars(
                query.order_by(CalculatorResultModel.calculator_result_id)
            )
        ]

    def get_calculator_result(self, run_id: UUID, result_id: UUID):
        row = self.session.get(CalculatorResultModel, result_id)
        if row is None or row.run_id != run_id:
            return None
        return CalculatorResultV1.model_validate(row.document)

    def list_results(self, run_id: UUID) -> list[MetricResultV1]:
        rows = self.session.scalars(
            select(MetricResultModel).where(
                MetricResultModel.run_id == run_id,
                MetricResultModel.schema_version == "1.0",
            )
        ).all()
        return [MetricResultV1.model_validate(row.document) for row in rows]

    def list_stage_runs(self, run_id: UUID) -> list[StageRunV1]:
        rows = self.session.scalars(
            select(StageRunModel)
            .where(StageRunModel.run_id == run_id)
            .order_by(StageRunModel.attempt, StageRunModel.stage_id)
        ).all()
        return [StageRunV1.model_validate(row.document) for row in rows]

    _DELETABLE_STATUSES = frozenset(
        {"SUCCEEDED", "FAILED", "PARTIAL_SUCCEEDED", "CANCELED"}
    )

    def delete_run(self, run_id: UUID) -> bool:
        """物理删除一条 EvaluationRun 及其专属产物。

        - 记录不存在时返回 False。
        - 非终态（PENDING / RUNNING / CANCEL_REQUESTED）禁止删除。
        - 在事务内按依赖顺序删除 QualityReport、RuleEvaluation、QualityIssue、
          IssueIndex、MetricResult、StageRun、EvaluationRun 与 PhysicalPlan，
          并清理 evaluation-runs/{run_id}/ 下的 artifact。
        """
        row = self.session.get(EvaluationRunModel, run_id, with_for_update=True)
        if row is None:
            return False

        if row.status not in self._DELETABLE_STATUSES:
            raise EvaluationRunNotDeletableError(
                status=row.status,
                message=f"评测任务状态为 {row.status}，未到达终态，无法删除",
            )

        report_ids = select(QualityReportModel.report_id).where(QualityReportModel.run_id == run_id)
        referenced = self.session.scalar(select(QualityReportModel.report_id).where(
            QualityReportModel.source_report_id.in_(report_ids)
            | QualityReportModel.source_document_report_id.in_(report_ids)
        ).limit(1))
        if referenced:
            raise EvaluationRunNotDeletableError(
                status=row.status, message="评测报告已被汇聚快照引用"
            )

        artifact_uris: set[str] = set()

        metric_rows = self.session.scalars(
            select(MetricResultModel).where(MetricResultModel.run_id == run_id)
        ).all()
        for metric_row in metric_rows:
            for evidence in metric_row.document.get("evidence", []):
                uri = evidence.get("artifact", {}).get("uri")
                if uri:
                    artifact_uris.add(uri)

        issue_index_rows = self.session.scalars(
            select(IssueIndexModel).where(IssueIndexModel.run_id == run_id)
        ).all()
        for index_row in issue_index_rows:
            uri = index_row.document.get("artifact", {}).get("uri")
            if uri:
                artifact_uris.add(uri)

        stage_rows = self.session.scalars(
            select(StageRunModel).where(StageRunModel.run_id == run_id)
        ).all()
        for stage_row in stage_rows:
            for artifact in stage_row.document.get("output_artifacts", []):
                uri = artifact.get("uri")
                if uri:
                    artifact_uris.add(uri)

        self.session.execute(
            delete(ScoredQualityReportModel).where(
                ScoredQualityReportModel.run_id == run_id
            )
        )
        self.session.execute(
            delete(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        self.session.execute(
            delete(RuleEvaluationModel).where(RuleEvaluationModel.run_id == run_id)
        )
        self.session.execute(
            delete(QualityIssueModel).where(QualityIssueModel.run_id == run_id)
        )
        self.session.execute(
            delete(IssueIndexModel).where(IssueIndexModel.run_id == run_id)
        )
        self.session.execute(
            delete(MetricResultModel).where(MetricResultModel.run_id == run_id)
        )
        self.session.execute(
            delete(StageRunModel).where(StageRunModel.run_id == run_id)
        )
        self.session.execute(
            delete(EvaluationRunModel).where(EvaluationRunModel.run_id == run_id)
        )
        self.session.execute(
            delete(PhysicalEvaluationPlanModel).where(
                PhysicalEvaluationPlanModel.physical_plan_id == row.physical_plan_id
            )
        )

        for uri in artifact_uris:
            self.artifact_store.delete(uri)
        self.artifact_store.delete_prefix(f"evaluation-runs/{run_id}/")

        return True

    def _calculator(self, key: str, version: str) -> CalculatorDescriptorV1:
        row = self.session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key=key, version=version
            )
        )
        if row is None:
            raise ValueError("Contract references an unavailable Calculator")
        return CalculatorDescriptorV1.model_validate(row.document)

    @staticmethod
    def _calculator_identity(calculator: CalculatorDescriptorV1) -> dict[str, Any]:
        return {
            "key": calculator.calculator_key,
            "version": calculator.version,
            "entrypoint": calculator.entrypoint,
            "code_digest": calculator.code_digest,
            "deterministic": calculator.deterministic,
            "execution_mode": calculator.execution.mode,
        }

    def _physical_output_binding(self, entry: dict[str, Any]) -> dict[str, Any]:
        item = entry["item"]
        calculator: CalculatorDescriptorV1 = entry["calculator"]
        metric_row = self.session.scalar(
            select(MetricDefinitionModel).filter_by(
                metric_key=item.metric.key, version=item.metric.version
            )
        )
        if metric_row is None:
            raise ValueError("Metric Definition disappeared during physical planning")
        metric = MetricDefinitionV1.model_validate(metric_row.document)
        execution = calculator.execution.model_dump(mode="json")
        role_overrides = entry["parameters"].get("content_roles", {})
        if isinstance(role_overrides, dict) and role_overrides:
            execution["input_roles"] = [
                {
                    **role,
                    "role": role_overrides.get(role["alias"], role["role"]),
                }
                for role in execution["input_roles"]
            ]
        execution["static_context"] = {
            **execution.get("static_context", {}),
            **entry["parameters"].get("static_context", {}),
            **{
                key: entry["parameters"][key]
                for key in execution.get("required_static_context", [])
                if key in entry["parameters"]
            },
        }
        return {
            "contract_item_key": item.key,
            "metric": {"key": item.metric.key, "version": item.metric.version},
            "measurement": item.metric.measurement,
            "calculator": self._calculator_identity(calculator),
            "parameters": entry["parameters"],
            "input_schema_ref": calculator.input_contract.sample_record_schema,
            "output_schema_ref": calculator.output_schema_ref,
            "execution": execution,
            "applicability": metric.applicability.model_dump(mode="json"),
            "logical_operation_id": entry["operation"].operation_id,
        }

    @staticmethod
    def _shared_parameters(bindings: list[dict[str, Any]]) -> dict[str, Any]:
        first = bindings[0]["parameters"]
        sharing = first.get("sharing", {})
        return {
            "content_role": first.get(
                "content_role", sharing.get("input_selector", "article.body.plain")
            ),
            "content_roles": {
                role["alias"]: role["role"]
                for binding in bindings
                for role in binding["execution"].get("input_roles", [])
            },
            "sample_limit": first.get("sample_limit", sharing.get("sample_limit", 20)),
            "max_input_chars": first.get(
                "max_input_chars", sharing.get("max_input_chars", 12000)
            ),
            "provider": sharing.get("provider"),
            "model": sharing.get("model"),
            "prompt_family": bindings[0]["execution"].get("prompt_family"),
        }

    @staticmethod
    def _shared_binding_preflight(binding, version) -> dict[str, Any] | None:
        refs = version.artifact_refs or {}
        mappings = refs.get("field_mapping", {}).get("mappings", [])
        targets = [
            item.get("target")
            for item in mappings
            if item.get("action") == "MAP" and isinstance(item.get("target"), dict)
        ]
        available_roles = {
            str(target.get("role")) for target in targets if target.get("role")
        }
        available_modalities = {
            str(target.get("modality")) for target in targets if target.get("modality")
        }
        if not available_modalities and available_roles:
            available_modalities = {"text"}
        expected_modalities = set(
            binding.get("applicability", {}).get("modalities", [])
        )
        if expected_modalities and not expected_modalities.intersection(
            available_modalities
        ):
            return {
                "status": "NOT_APPLICABLE",
                "reason_code": "MODALITY_NOT_APPLICABLE",
                "expected": sorted(expected_modalities),
                "actual": sorted(available_modalities),
            }
        required_roles = {
            role["role"]
            for role in binding["execution"].get("input_roles", [])
            if role.get("required", True)
        }
        missing_roles = sorted(required_roles - available_roles)
        if missing_roles:
            return {
                "status": "MISSING",
                "reason_code": "REQUIRED_CONTENT_ROLE_MISSING",
                "missing_roles": missing_roles,
            }
        execution = binding["execution"]
        missing_context = sorted(
            set(execution.get("required_static_context", []))
            - set(execution.get("static_context", {}))
        )
        if missing_context:
            return {
                "status": "MISSING",
                "reason_code": "REQUIRED_STATIC_CONTEXT_MISSING",
                "missing_context": missing_context,
            }
        return None

    @staticmethod
    def _resolved_parameters(calculator, configured):
        defaults = {
            key: spec["default"]
            for key, spec in calculator.parameter_schema.get("properties", {}).items()
            if "default" in spec
        }
        defaults.update(configured)
        return defaults

    @staticmethod
    def _validate_parameter_schema(calculator, parameters) -> None:
        validator = Draft202012Validator(calculator.parameter_schema)
        errors = sorted(
            validator.iter_errors(parameters), key=lambda item: list(item.path)
        )
        if errors:
            first = errors[0]
            location = ".".join(str(item) for item in first.path) or "$"
            raise CalculatorValidationError(
                f"Invalid Calculator parameters at {location}: {first.message}",
                location=location,
                validator=first.validator,
            )

    @staticmethod
    def _save_run(row, run):
        row.status = run.status
        row.started_at = run.started_at
        row.finished_at = run.finished_at
        row.document = run.model_dump(mode="json")

"""Read-only scoring inputs. P1 fields absent from the persisted schemas are explicit."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.evidence.index import IndexNotReadyError
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.logical_planner_service import semantic_fingerprint
from qa_eval.contracts.base import JsonObject, WireModel
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
    CalculatorOutputSchemaV1,
    CalculatorResultV1,
    Count,
)
from qa_eval.contracts.execution import (
    EvaluationRunV1,
    LogicalEvaluationPlanV1,
    LogicalOutputBindingV1,
    PhysicalEvaluationPlanV1,
    PhysicalStageV1,
    StageRunV1,
)
from qa_eval.contracts.metrics import EvidenceArtifact, MetricResultV1, VersionedKey
from qa_eval.contracts.quality import (
    NativeEvaluationItemV1,
    NativeRuleEvaluationV1,
    QualityContractVersionV1,
    QualityIssueV1,
    RuleEvaluationV1,
)
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
    QualityIssueModel,
    RuleEvaluationModel,
)

Status = Literal["OK", "MISSING", "ERROR", "PARTIAL", "NA"]
Result = CalculatorResultV1 | MetricResultV1
Rule = NativeRuleEvaluationV1 | RuleEvaluationV1
DecisionStatus = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
DECISION_STATES = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")


class MetricNode(WireModel):
    metric: VersionedKey
    dimension: Literal["DOCUMENTATION", "DATA_QUALITY", "MODEL_APPLICATION"]
    parent_key: str | None = None
    required: bool = True


class RecordCounts(WireModel):
    population: Count
    passed: Count
    failed: Count
    unknown: Count
    not_applicable: Count

    @property
    def coverage_basis(self) -> tuple[int, int]:
        known = self.passed + self.failed
        return known, known + self.unknown


class FrozenScoringConfig(WireModel):
    mapping_id: str = Field(min_length=1)
    mapping_version: str = Field(min_length=1)
    statistic_keys: list[str] = Field(min_length=1)
    mapping: JsonObject = Field(default_factory=dict)
    coverage_min: float | None = Field(default=None, strict=True, ge=0, le=1)
    grain: Literal["RECORD", "GROUP", "DATASET", "DOCUMENT"]
    coverage_statistic: str | None = None
    coverage_na_approval: str | None = None
    # The actual future schema must define this binding. Fixtures make it explicit.
    count_keys: dict[
        Literal["population", "passed", "failed", "unknown", "not_applicable"], str
    ] = Field(default_factory=dict)
    applicability: Literal["APPLICABLE", "NOT_APPLICABLE"] = "APPLICABLE"
    applicability_reason: str | None = None


class ScoringContractFixture(WireModel):
    """Isolated P1 inputs pending versioned Contract and Run publication support."""

    source: Literal["FIXTURE"]
    run_id: UUID
    plan_fingerprint: str
    contract_fingerprint: str
    mode: Literal["BASIC"]
    nodes: list[MetricNode] = Field(min_length=1)
    slots: dict[str, FrozenScoringConfig]


@dataclass(frozen=True)
class ComponentKey:
    item_key: str
    output_slot_id: str
    schema_fingerprint: str | None


@dataclass(frozen=True)
class ScoringComponent:
    key: ComponentKey
    metric: VersionedKey
    stage_id: str | None
    stage_run_id: UUID | None
    source: Literal["NATIVE", "LEGACY"]
    status: Status
    reasons: tuple[str, ...] = ()
    result: Result | None = None
    statistics: JsonObject | None = None
    configuration: FrozenScoringConfig | None = None
    counts: RecordCounts | None = None

    @property
    def artifacts(self) -> tuple[EvidenceArtifact, ...]:
        if isinstance(self.result, CalculatorResultV1):
            return (
                *self.result.decision_artifact_refs,
                *self.result.evidence_artifact_refs,
            )
        return (
            tuple(self.result.evidence)
            if isinstance(self.result, MetricResultV1)
            else ()
        )


@dataclass(frozen=True)
class ScoringInputs:
    contract: QualityContractVersionV1
    plan: PhysicalEvaluationPlanV1
    run: EvaluationRunV1
    nodes: tuple[MetricNode, ...]
    components: tuple[ScoringComponent, ...]
    missing_metrics: tuple[VersionedKey, ...]
    blockers: tuple[str, ...]

    @property
    def formal_ready(self) -> bool:
        return (
            not self.blockers
            and not self.missing_metrics
            and all(
                item.source == "NATIVE" and item.status in {"OK", "NA"}
                for item in self.components
            )
        )

    def coverage_policy(self, key: ComponentKey):
        return next(
            (
                item.coverage_policy
                for item in self.contract.observation_items
                if item.key == key.item_key
            ),
            None,
        )

    def statistic(self, key: ComponentKey, statistic_key: str) -> object:
        component = next((item for item in self.components if item.key == key), None)
        if component is None or component.statistics is None:
            raise KeyError((key, statistic_key))
        return component.statistics[statistic_key]


@dataclass(frozen=True)
class ExpectedOutput:
    stage: PhysicalStageV1
    binding: LogicalOutputBindingV1
    schema: CalculatorOutputSchemaV1 | None

    @property
    def key(self) -> ComponentKey:
        return ComponentKey(
            self.binding.contract_item_key,
            self.binding.output_slot_id or self.binding.contract_item_key,
            NativeEvaluationItemV1.schema_fingerprint(self.schema)
            if self.schema
            else None,
        )


class ScoringConsumer:
    def __init__(self, session: Session, evidence: EvidenceQueryService):
        self.session = session
        self.evidence = evidence

    def read(
        self,
        run_id: UUID,
        *,
        selected_attempts: dict[str, UUID] | None = None,
        fixture: ScoringContractFixture | None = None,
    ) -> ScoringInputs:
        """Use the sealed selection or an explicit historical diagnostic selection."""
        contract, plan, run = self._read_frozen(run_id)
        frozen_selection = run.execution.get("selected_attempts")
        if frozen_selection is not None:
            persisted_attempts = {
                key: UUID(value) for key, value in frozen_selection.items()
            }
            if (
                selected_attempts is not None
                and selected_attempts != persisted_attempts
            ):
                raise ValueError("attempt selection differs from the sealed Run")
            selected_attempts = persisted_attempts
        selected_attempts = selected_attempts or {}
        expected = self._expected(contract, plan)
        stages = {stage.stage_id for stage in plan.stages}
        if selected_attempts.keys() - stages or len(
            set(selected_attempts.values())
        ) != len(selected_attempts):
            raise ValueError("invalid selected attempt map")
        outputs_by_stage = Counter(
            (
                output.stage.stage_id,
                output.binding.metric.key,
                output.binding.metric.version,
            )
            for output in expected
            if output.schema is None
        )
        nodes, missing = self._tree(run, contract, plan, expected, fixture)
        components = tuple(
            self._component(
                run,
                plan,
                output,
                selected_attempts.get(output.stage.stage_id),
                fixture.slots.get(output.key.output_slot_id) if fixture else None,
                outputs_by_stage[
                    output.stage.stage_id,
                    output.binding.metric.key,
                    output.binding.metric.version,
                ]
                > 1,
            )
            for output in expected
        )
        planned_items = {output.key.item_key for output in expected}
        components += tuple(
            ScoringComponent(
                key=ComponentKey(
                    item.key,
                    item.statistic_ref.output_slot_id
                    if isinstance(item, NativeEvaluationItemV1)
                    else item.key,
                    item.output_schema_fingerprint
                    if isinstance(item, NativeEvaluationItemV1)
                    else None,
                ),
                metric=VersionedKey(key=item.metric.key, version=item.metric.version),
                stage_id=None,
                stage_run_id=None,
                source=source,
                status="MISSING",
                reasons=("CONTRACT_ITEM_NOT_PLANNED",),
            )
            for source, items in (
                ("LEGACY", contract.items),
                ("NATIVE", contract.observation_items),
            )
            for item in items
            if item.key not in planned_items
        )
        blockers = (
            ("P1_FREEZE_FIXTURE_ONLY",)
            if fixture
            else (
                "P1_FROZEN_SCORING_CONFIGURATION_UNAVAILABLE",
                *(
                    ("P1_PERSISTED_ATTEMPT_SELECTION_UNAVAILABLE",)
                    if frozen_selection is None
                    else ()
                ),
            )
        )
        return ScoringInputs(contract, plan, run, nodes, components, missing, blockers)

    def _read_frozen(self, run_id: UUID):
        run_row = self.session.get(EvaluationRunModel, run_id)
        if run_row is None:
            raise LookupError(run_id)
        run = EvaluationRunV1.model_validate(run_row.document)
        plan_row = self.session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        contract_row = self.session.get(
            QualityContractVersionModel, run.contract_version_id
        )
        if plan_row is None or contract_row is None:
            raise ValueError("missing frozen plan or contract")
        plan = PhysicalEvaluationPlanV1.model_validate(plan_row.document)
        contract = QualityContractVersionV1.model_validate(contract_row.document)
        semantic = {
            "dataset_version_id": str(plan.dataset_version_id),
            "contract_version_id": str(plan.contract_version_id),
            "stages": plan_row.document["stages"],
        }
        # Older writers omitted default fields in the hashed stage document.
        if (
            run.run_id != run_id
            or (run.physical_plan_id, run.dataset_version_id, run.contract_version_id)
            != (
                run_row.physical_plan_id,
                run_row.dataset_version_id,
                run_row.contract_version_id,
            )
            or (
                plan.physical_plan_id,
                plan.dataset_version_id,
                plan.contract_version_id,
            )
            != (run.physical_plan_id, run.dataset_version_id, run.contract_version_id)
            or (
                plan_row.dataset_version_id,
                plan_row.contract_version_id,
                plan_row.logical_plan_id,
            )
            != (plan.dataset_version_id, plan.contract_version_id, plan.logical_plan_id)
            or contract.contract_version_id != run.contract_version_id
            or contract.contract_id != contract_row.contract_id
            or contract.fingerprint != contract_row.fingerprint
            or run.status != run_row.status
            or contract.status == "DRAFT"
            or contract_row.target_dataset_version_id != run.dataset_version_id
            or plan.semantic_fingerprint != plan_row.semantic_fingerprint
            or not self._matches_plan_fingerprint(semantic, plan.semantic_fingerprint)
        ):
            raise ValueError("run, plan or contract ownership/fingerprint mismatch")
        return contract, plan, run

    @staticmethod
    def _matches_plan_fingerprint(semantic: dict, fingerprint: str) -> bool:
        if semantic_fingerprint(semantic) == fingerprint:
            return True
        stages = [
            {
                key: value
                for key, value in stage.items()
                if (key, value)
                not in (("shared_computation_id", None), ("output_bindings", []))
            }
            for stage in semantic["stages"]
        ]
        return semantic_fingerprint({**semantic, "stages": stages}) == fingerprint

    def _expected(
        self, contract: QualityContractVersionV1, plan: PhysicalEvaluationPlanV1
    ):
        logical = None
        if plan.logical_plan_id is not None:
            row = self.session.get(LogicalEvaluationPlanModel, plan.logical_plan_id)
            if row is None:
                raise ValueError("missing frozen logical plan")
            logical = LogicalEvaluationPlanV1.model_validate(row.document)
            if (
                logical.logical_plan_id,
                logical.contract_id,
                logical.contract_version_id,
                logical.dataset_version_id,
                logical.semantic_fingerprint,
            ) != (
                plan.logical_plan_id,
                contract.contract_id,
                plan.contract_version_id,
                plan.dataset_version_id,
                row.semantic_fingerprint,
            ) or (row.contract_version_id, row.dataset_version_id) != (
                plan.contract_version_id,
                plan.dataset_version_id,
            ):
                raise ValueError("invalid logical plan ownership")
        legacy_items = {item.key: item for item in contract.items}
        native_items = {item.key: item for item in contract.observation_items}
        operations = (
            {item.operation_id: item for item in logical.operations} if logical else {}
        )
        outputs = []
        for stage in plan.stages:
            bindings = stage.output_bindings
            if not bindings:
                bindings = [
                    binding.model_dump(mode="json")
                    for op_id in stage.logical_operation_ids
                    for binding in operations[op_id].output_bindings
                ]
            for raw in bindings:
                binding = LogicalOutputBindingV1.model_validate(
                    {
                        key: raw[key]
                        for key in LogicalOutputBindingV1.model_fields
                        if key in raw
                    }
                )
                schema = None
                items = legacy_items
                if binding.output_kind == "CALCULATOR_RESULT":
                    schema = CalculatorOutputSchemaV1.model_validate(
                        raw.get("native_output")
                    )
                    items = native_items
                item = items.get(binding.contract_item_key)
                if item is None or (binding.metric.key, binding.metric.version) != (
                    item.metric.key,
                    item.metric.version,
                ):
                    raise ValueError("output binding does not match frozen contract")
                if schema and isinstance(item, NativeEvaluationItemV1):
                    item.validate_output_schema(schema)
                    if binding.output_slot_id != item.statistic_ref.output_slot_id:
                        raise ValueError("native rule slot mismatch")
                if schema is None and binding.measurement != item.metric.measurement:
                    raise ValueError("legacy measurement differs from frozen contract")
                outputs.append(ExpectedOutput(stage, binding, schema))
        if len({stage.stage_id for stage in plan.stages}) != len(plan.stages) or (
            len({output.key.output_slot_id for output in outputs}) != len(outputs)
            or len({output.key.item_key for output in outputs}) != len(outputs)
        ):
            raise ValueError("duplicate stage, output slot or contract item")
        self._guard_0208(
            {item.metric.key for item in [*contract.items, *contract.observation_items]}
        )
        self._guard_0208({output.binding.metric.key for output in outputs})
        return outputs

    @staticmethod
    def _guard_0208(keys: set[str]) -> None:
        if "0208" in keys and any(key.startswith("020801") for key in keys):
            raise ValueError("0208 parent and child execution are mutually exclusive")

    @staticmethod
    def _tree(run, contract, plan, expected, fixture):
        metrics = {
            (output.binding.metric.key, output.binding.metric.version)
            for output in expected
        }
        if fixture is None:
            missing = tuple(
                VersionedKey(key=item.metric.key, version=item.metric.version)
                for item in [*contract.items, *contract.observation_items]
                if (item.metric.key, item.metric.version) not in metrics
            )
            return (), missing
        if (fixture.run_id, fixture.plan_fingerprint, fixture.contract_fingerprint) != (
            run.run_id,
            plan.semantic_fingerprint,
            contract.fingerprint,
        ):
            raise ValueError("fixture does not match frozen run")
        nodes = {node.metric.key: node for node in fixture.nodes}
        if len(nodes) != len(fixture.nodes):
            raise ValueError("duplicate metric node")
        if fixture.slots.keys() - {output.key.output_slot_id for output in expected}:
            raise ValueError("scoring configuration references an unknown slot")
        parents = {
            node.parent_key for node in fixture.nodes if node.parent_key is not None
        }
        if any(key in parents for key, _ in metrics):
            raise ValueError(
                "a node cannot have both execution outputs and child metrics"
            )
        basic_roots = {
            "DOCUMENTATION": {f"010{i}" for i in range(1, 5)},
            "DATA_QUALITY": {f"020{i}" for i in range(1, 9)},
        }
        if any(node.dimension not in basic_roots for node in fixture.nodes) or any(
            {
                node.metric.key
                for node in fixture.nodes
                if node.dimension == dimension
                and node.parent_key is None
                and node.required
            }
            != required
            for dimension, required in basic_roots.items()
        ):
            raise ValueError("fixture must enumerate the required BASIC roots")
        for node in fixture.nodes:
            seen = {node.metric.key}
            parent = node.parent_key
            while parent is not None:
                if (
                    parent in seen
                    or parent not in nodes
                    or nodes[parent].dimension != node.dimension
                ):
                    raise ValueError("invalid metric tree")
                seen.add(parent)
                parent = nodes[parent].parent_key
        if any(
            key not in nodes or nodes[key].metric.version != version
            for key, version in metrics
        ):
            raise ValueError("planned output is outside the metric tree")
        missing = tuple(
            node.metric
            for node in fixture.nodes
            if node.required
            and node.dimension == "DATA_QUALITY"
            and node.metric.key not in parents
            and (node.metric.key, node.metric.version) not in metrics
        )
        return tuple(fixture.nodes), missing

    def _component(self, run, plan, output, attempt_id, config, ambiguous_legacy):
        source = "NATIVE" if output.schema else "LEGACY"
        base = {
            "key": output.key,
            "metric": output.binding.metric,
            "stage_id": output.stage.stage_id,
            "stage_run_id": attempt_id,
            "source": source,
            "configuration": config,
        }
        if attempt_id is None:
            return ScoringComponent(
                **base, status="MISSING", reasons=("ATTEMPT_NOT_SELECTED",)
            )
        try:
            attempt = self._attempt(run, plan, output.stage, attempt_id)
            if attempt.status == "FAILED":
                return ScoringComponent(
                    **base, status="ERROR", reasons=("STAGE_FAILED",)
                )
            if attempt.status != "SUCCEEDED":
                return ScoringComponent(
                    **base, status="PARTIAL", reasons=("STAGE_UNFINISHED",)
                )
            result = self._result(run, plan, output, attempt_id, ambiguous_legacy)
            if result is None:
                return ScoringComponent(
                    **base, status="MISSING", reasons=("RESULT_MISSING",)
                )
            if result.status in ("ERROR", "MISSING"):
                return ScoringComponent(**base, status=result.status, result=result)
            if isinstance(result, MetricResultV1):
                return ScoringComponent(
                    **base,
                    status="PARTIAL",
                    result=result,
                    statistics={item.key: item.value for item in result.observations},
                    reasons=("LEGACY_DECISION_PROOF_UNAVAILABLE", result.status),
                )
            return self._native_component(base, result, attempt, config)
        except (ValueError, KeyError, OSError) as exc:
            return ScoringComponent(**base, status="ERROR", reasons=(str(exc),))

    def _attempt(self, run, plan, stage, attempt_id):
        row = self.session.get(StageRunModel, attempt_id)
        if row is None:
            raise ValueError("selected attempt does not exist")
        attempt = StageRunV1.model_validate(row.document)
        if (
            (
                attempt.stage_run_id,
                attempt.run_id,
                attempt.physical_plan_id,
                attempt.stage_id,
                attempt.attempt,
                attempt.status,
            )
            != (
                attempt_id,
                run.run_id,
                plan.physical_plan_id,
                stage.stage_id,
                row.attempt,
                row.status,
            )
            or (row.run_id, row.physical_plan_id, row.stage_id)
            != (run.run_id, plan.physical_plan_id, stage.stage_id)
            or attempt.input_fingerprint != run.input_fingerprint
            or attempt.calculator != stage.calculator
            or attempt.resolved_parameters != stage.resolved_parameters
            or attempt.parameters_fingerprint
            != semantic_fingerprint(stage.resolved_parameters)
        ):
            raise ValueError("selected attempt ownership or fingerprint mismatch")
        return attempt

    def _result(self, run, plan, output, attempt_id, ambiguous_legacy) -> Result | None:
        binding = output.binding
        if output.schema:
            rows = self.session.scalars(
                select(CalculatorResultModel).where(
                    CalculatorResultModel.stage_run_id == attempt_id,
                    CalculatorResultModel.output_slot_id == binding.output_slot_id,
                )
            ).all()
            if not rows:
                return None
            (row,) = rows
            result = CalculatorResultV1.model_validate(row.document)
            if (
                result.calculator_result_id != row.calculator_result_id
                or (result.contract_item_key, result.output_slot_id)
                != (binding.contract_item_key, binding.output_slot_id)
                or row.contract_item_key != binding.contract_item_key
                or result.output_schema != output.schema
                or result.plan_fingerprint != plan.semantic_fingerprint
                or result.input_fingerprint != run.input_fingerprint
                or (result.calculator_key, result.calculator_version)
                != (
                    output.stage.calculator.get("key"),
                    output.stage.calculator.get("version"),
                )
            ):
                raise ValueError("native result identity or fingerprint mismatch")
        else:
            if ambiguous_legacy:
                raise ValueError(
                    "legacy results cannot disambiguate outputs for this stage and metric"
                )
            rows = self.session.scalars(
                select(MetricResultModel).where(
                    MetricResultModel.stage_run_id == attempt_id,
                    MetricResultModel.metric_key == binding.metric.key,
                    MetricResultModel.metric_version == binding.metric.version,
                )
            ).all()
            if not rows:
                return None
            (row,) = rows
            result = MetricResultV1.model_validate(row.document)
            if (
                result.metric_result_id != row.metric_result_id
                or row.dataset_version_id != run.dataset_version_id
                or row.status != result.status
            ):
                raise ValueError("legacy result identity mismatch")
            if result.status in {"OK", "PARTIAL"} and binding.measurement not in {
                item.key for item in result.observations
            }:
                raise ValueError("legacy measurement missing from result")
        if (
            result.run_id,
            result.stage_run_id,
            result.dataset_version_id,
            result.metric,
        ) != (
            run.run_id,
            attempt_id,
            run.dataset_version_id,
            binding.metric,
        ) or row.run_id != run.run_id:
            raise ValueError("result ownership mismatch")
        return result

    def _native_component(self, base, result, attempt, config):
        self._validate_artifacts(result, attempt)
        facts = {"result": result, "statistics": result.statistics}
        if config is None:
            return ScoringComponent(
                **base,
                **facts,
                status="PARTIAL",
                reasons=("FROZEN_SCORING_CONFIG_MISSING",),
            )
        if config.applicability == "NOT_APPLICABLE":
            if not config.applicability_reason or result.status != "NOT_APPLICABLE":
                raise ValueError("applicability claim is unsupported")
            return ScoringComponent(**base, **facts, status="NA")
        if result.status == "NOT_APPLICABLE":
            raise ValueError("unexpected calculator not-applicable result")
        if set(config.statistic_keys) - result.statistics.keys():
            raise ValueError("frozen scoring statistic is missing")
        if config.grain != "RECORD":
            return ScoringComponent(**base, **facts, status=result.status)
        keys = config.count_keys
        if len(keys) != 5 or len(set(keys.values())) != 5:
            raise ValueError("four-state population binding is incomplete or ambiguous")
        declared_counts = {
            item.key for item in result.output_schema.statistics if item.kind == "count"
        }
        if set(keys.values()) - declared_counts:
            raise ValueError("four-state population must use count statistics")
        counts = RecordCounts.model_validate(
            {field: result.statistics[key] for field, key in keys.items()}
        )
        if (
            counts.population
            != counts.passed + counts.failed + counts.unknown + counts.not_applicable
        ):
            raise ValueError("four-state population does not close")
        reasons = []
        if not counts.coverage_basis[0]:
            reasons.append("ZERO_SCORE_DENOMINATOR")
        if counts.unknown:
            reasons.append("UNKNOWN_RECORDS")
        if result.status == "PARTIAL":
            reasons.append("CALCULATOR_PARTIAL")
        if result.output_schema.decision_retention != "ALL":
            reasons.append("COMPLETE_DECISION_PROOF_UNAVAILABLE")
        else:
            try:
                self._validate_all_decisions(result, counts, keys["population"])
            except IndexNotReadyError:
                reasons.append("DECISION_OR_EVIDENCE_INDEX_NOT_READY")
        return ScoringComponent(
            **base,
            **facts,
            counts=counts,
            status="PARTIAL" if reasons else "OK",
            reasons=tuple(reasons),
        )

    def _validate_artifacts(
        self, result: CalculatorResultV1, attempt: StageRunV1
    ) -> None:
        published = {
            str(item.get("artifact_id")): item for item in attempt.output_artifacts
        }
        seen = set()
        for ref in [*result.decision_artifact_refs, *result.evidence_artifact_refs]:
            row = self.session.get(CalculatorResultArtifactModel, ref.evidence_id)
            if (
                ref.evidence_id in seen
                or row is None
                or row.calculator_result_id != result.calculator_result_id
                or published.get(str(ref.artifact.artifact_id))
                != ref.artifact.model_dump(mode="json")
                or (
                    ref.artifact.record_count is not None
                    and ref.artifact.record_count != ref.summary.record_count
                )
            ):
                raise ValueError("invalid decision or evidence artifact reference")
            seen.add(ref.evidence_id)

    def _validate_all_decisions(self, result, counts, population_key):
        refs = result.decision_artifact_refs
        if (
            (counts.population > 0 and not refs)
            or result.output_schema.decision_population_key != population_key
            or sum(ref.summary.record_count for ref in refs) != counts.population
        ):
            raise ValueError("decision artifact coverage does not match population")
        artifacts = {}
        for ref in result.evidence_artifact_refs:
            key = ref.summary.attributes.get("artifact_key")
            if key is not None:
                if not isinstance(key, str) or key in artifacts:
                    raise ValueError(
                        "invalid or duplicate decision evidence artifact key"
                    )
                artifacts[key] = ref.evidence_id
        observed = Counter()
        subjects = set()
        for ref in refs:
            if ref.schema_ref != DECISION_SCHEMA_REF:
                raise ValueError("unsupported ALL decision schema")
            cursor = None
            while True:
                page = self.evidence.list_evidence_rows(
                    ref.evidence_id, limit=200, cursor=cursor
                )
                if page is None:
                    raise ValueError("decision artifact cannot be located")
                rows, cursor = page
                for row in rows:
                    decision = CalculatorDecisionV1.model_validate(row)
                    if (
                        decision.status not in DECISION_STATES
                        or decision.sample_id in subjects
                    ):
                        raise ValueError("invalid or duplicate final record decision")
                    subjects.add(decision.sample_id)
                    observed[decision.status] += 1
                    for reference in decision.evidence_refs:
                        evidence_id = artifacts.get(reference.artifact_key)
                        if evidence_id is None or not self.evidence_row(
                            result, evidence_id, reference.row_id
                        ):
                            raise ValueError(
                                "decision references an unknown evidence row"
                            )
                if cursor is None:
                    break
        if tuple(observed[state] for state in DECISION_STATES) != (
            counts.passed,
            counts.failed,
            counts.unknown,
            counts.not_applicable,
        ):
            raise ValueError("decision rows do not match four-state statistics")

    def rules(self, inputs: ScoringInputs, key: ComponentKey) -> tuple[Rule, ...]:
        component = self._resolved_component(inputs, key)
        result = component.result
        source = (
            RuleEvaluationModel.calculator_result_id == result.calculator_result_id
            if isinstance(result, CalculatorResultV1)
            else RuleEvaluationModel.metric_result_id == result.metric_result_id
        )
        rows = self.session.scalars(select(RuleEvaluationModel).where(source)).all()
        rules = []
        for row in rows:
            rule_type = (
                NativeRuleEvaluationV1
                if component.source == "NATIVE"
                else RuleEvaluationV1
            )
            rule = rule_type.model_validate(row.document)
            if (
                rule.rule_evaluation_id != row.rule_evaluation_id
                or (rule.run_id, rule.dataset_version_id, rule.contract_version_id)
                != (
                    inputs.run.run_id,
                    inputs.run.dataset_version_id,
                    inputs.contract.contract_version_id,
                )
                or row.run_id != inputs.run.run_id
                or (rule.metric.key, rule.metric.version)
                != (component.metric.key, component.metric.version)
            ):
                raise ValueError("rule evaluation ownership mismatch")
            if isinstance(rule, NativeRuleEvaluationV1):
                if rule.calculator_result_id != result.calculator_result_id:
                    raise ValueError("rule evaluation result mismatch")
                self._check_statistic_ref(result, rule.statistic_ref)
                if (rule.calculator.key, rule.calculator.version) != (
                    result.calculator_key,
                    result.calculator_version,
                ) or rule.statistic not in result.output_schema.statistics:
                    raise ValueError("rule evaluation statistic schema mismatch")
            elif (
                rule.metric_result_id != result.metric_result_id
                or rule.contract_item_key != component.key.item_key
            ):
                raise ValueError("legacy rule evaluation result mismatch")
            rules.append(rule)
        return tuple(rules)

    def issues(
        self,
        inputs: ScoringInputs,
        key: ComponentKey,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[QualityIssueV1, ...]:
        result = self._resolved_component(inputs, key).result
        native = isinstance(result, CalculatorResultV1)
        source = (
            QualityIssueModel.calculator_result_id == result.calculator_result_id
            if native
            else QualityIssueModel.metric_result_id == result.metric_result_id
        )
        rows = self.session.scalars(
            select(QualityIssueModel)
            .where(source)
            .order_by(QualityIssueModel.issue_id)
            .limit(limit)
            .offset(offset)
        ).all()
        issues = []
        for row in rows:
            issue = QualityIssueV1.model_validate(row.document)
            if (issue.issue_id, issue.run_id, issue.dataset_version_id, row.run_id) != (
                row.issue_id,
                inputs.run.run_id,
                inputs.run.dataset_version_id,
                inputs.run.run_id,
            ) or (issue.source.metric_key, issue.source.metric_version) != (
                result.metric.key,
                result.metric.version,
            ):
                raise ValueError("issue ownership mismatch")
            if native:
                if (
                    getattr(issue.source, "calculator_result_id", None)
                    != result.calculator_result_id
                ):
                    raise ValueError("issue result mismatch")
                self._check_statistic_ref(result, issue.source.statistic_ref)
            elif (
                getattr(issue.source, "metric_result_id", None)
                != result.metric_result_id
            ):
                raise ValueError("legacy issue result mismatch")
            for ref in issue.evidence:
                self._artifact_ref(result, ref.evidence_id)
            issues.append(issue)
        return tuple(issues)

    @staticmethod
    def _check_statistic_ref(result, ref):
        if (ref.contract_item_key, ref.output_slot_id, ref.calculator_version) != (
            result.contract_item_key,
            result.output_slot_id,
            result.calculator_version,
        ) or ref.statistics_key not in {
            item.key for item in result.output_schema.statistics
        }:
            raise ValueError("native statistic reference mismatch")

    @staticmethod
    def _resolved_component(
        inputs: ScoringInputs, key: ComponentKey
    ) -> ScoringComponent:
        component = next((item for item in inputs.components if item.key == key), None)
        if component is None or component.result is None:
            raise KeyError(key)
        return component

    @staticmethod
    def _artifact_ref(result: Result, evidence_id: UUID) -> EvidenceArtifact:
        refs = (
            [*result.decision_artifact_refs, *result.evidence_artifact_refs]
            if isinstance(result, CalculatorResultV1)
            else result.evidence
        )
        ref = next((item for item in refs if item.evidence_id == evidence_id), None)
        if ref is None:
            raise ValueError("evidence reference belongs to another result")
        return ref

    def evidence_row(
        self, result: Result, evidence_id: UUID, row_id: str
    ) -> JsonObject | None:
        self._artifact_ref(result, evidence_id)
        resolved = self.evidence.list_evidence_rows(evidence_id, limit=1, row_id=row_id)
        if resolved is None or not resolved[0]:
            return None
        row = resolved[0][0]
        if row.get("row_id") != row_id:
            raise ValueError("indexed evidence row identity mismatch")
        return row

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorError,
    CalculatorValidationError,
)
from qa_eval.contracts.execution import (
    EvaluationRunV1,
    PhysicalEvaluationPlanV1,
    StageRunV1,
)
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.contracts.registry import CalculatorDescriptorV1
from qa_eval.domain.exceptions import EvaluationRunNotDeletableError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
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
)
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel
from qa_eval.infrastructure.uuid7 import uuid7


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _sha(value: str | None) -> str:
    if not value:
        return f"sha256:{'0' * 64}"
    return value if value.startswith("sha256:") else f"sha256:{value}"


class EvaluationRunService:
    def __init__(self, session: Session, artifact_store: ArtifactStore):
        self.session = session
        self.artifact_store = artifact_store
        self.calculator_loader = TrustedCalculatorLoader()

    def create(self, contract_version_id: UUID) -> EvaluationRunV1:
        contract_row = self.session.get(
            QualityContractVersionModel, contract_version_id
        )
        if contract_row is None:
            raise ValueError("Published Quality Contract Version not found")
        contract = QualityContractVersionV1.model_validate(contract_row.document)
        if contract.status != "PUBLISHED":
            raise ValueError("Only PUBLISHED Quality Contract Versions can run")
        version = self.session.get(
            DatasetVersionModel, contract_row.target_dataset_version_id
        )
        if version is None or version.status != "PUBLISHED":
            raise ValueError("EvaluationRun requires a PUBLISHED DatasetVersion")
        if not version.artifact_refs or "normalized" not in version.artifact_refs:
            raise ValueError("DatasetVersion has no normalized input artifact")

        stages = []
        for order, item in enumerate(contract.items):
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
            stages.append(
                {
                    "stage_id": f"metric-{order}-{item.metric.key}",
                    "order": order,
                    "metric": {
                        "key": item.metric.key,
                        "version": item.metric.version,
                    },
                    "calculator": {
                        "key": calculator.calculator_key,
                        "version": calculator.version,
                        "entrypoint": calculator.entrypoint,
                        "code_digest": calculator.code_digest,
                        "deterministic": calculator.deterministic,
                    },
                    "resolved_parameters": parameters,
                    "input_schema_ref": calculator.input_contract.sample_record_schema,
                    "output_schema_ref": calculator.output_schema_ref,
                }
            )
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
                "input_fingerprint": _sha(version.content_fingerprint),
                "created_at": now,
            }
        )
        self.session.add(
            PhysicalEvaluationPlanModel(
                physical_plan_id=plan_id,
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

    def execute(self, run_id: UUID) -> EvaluationRunV1:
        row = self.session.get(EvaluationRunModel, run_id)
        if row is None:
            raise ValueError("EvaluationRun not found")
        run = EvaluationRunV1.model_validate(row.document)
        if run.status != "PENDING":
            raise ValueError("Only PENDING EvaluationRuns can start")
        plan_row = self.session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        version = self.session.get(DatasetVersionModel, run.dataset_version_id)
        if plan_row is None or version is None:
            raise ValueError("EvaluationRun references missing execution inputs")
        plan = PhysicalEvaluationPlanV1.model_validate(plan_row.document)
        run = run.model_copy(
            update={"status": "RUNNING", "started_at": datetime.now(UTC)}
        )
        self._save_run(row, run)
        self.session.commit()

        failures = 0
        for stage in sorted(plan.stages, key=lambda item: item.order):
            if not self._execute_stage(run, stage, version):
                failures += 1

        finished = datetime.now(UTC)
        status = (
            "FAILED"
            if failures == len(plan.stages)
            else "PARTIAL_SUCCEEDED"
            if failures
            else "SUCCEEDED"
        )
        run = run.model_copy(
            update={
                "status": status,
                "finished_at": finished,
                "error": {"failed_stage_count": failures} if failures else None,
            }
        )
        self._save_run(row, run)
        self.session.commit()
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
                        "phase": "QUALITY_RESULT_FINALIZATION",
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            self._save_run(row, run)
            self.session.commit()
        return run

    def _execute_stage(self, run, stage, version: DatasetVersionModel) -> bool:
        now = datetime.now(UTC)
        stage_run_id = uuid7()
        params_fingerprint = _fingerprint(stage.resolved_parameters)
        stage_run = StageRunV1.model_validate(
            {
                "stage_run_id": stage_run_id,
                "run_id": run.run_id,
                "physical_plan_id": run.physical_plan_id,
                "stage_id": stage.stage_id,
                "attempt": 1,
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
            attempt=1,
            status="RUNNING",
            document=stage_run.model_dump(mode="json"),
        )
        self.session.add(stage_row)
        self.session.commit()
        try:
            result, output_artifacts, diagnostics = self._calculate(
                run, stage_run, stage, version
            )
            self.session.add(
                MetricResultModel(
                    metric_result_id=result.metric_result_id,
                    run_id=result.run_id,
                    stage_run_id=result.stage_run_id,
                    dataset_version_id=result.dataset_version_id,
                    metric_key=result.metric.key,
                    metric_version=result.metric.version,
                    status=result.status,
                    document=result.model_dump(mode="json"),
                )
            )
            stage_run = stage_run.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "finished_at": datetime.now(UTC),
                    "output_artifacts": output_artifacts,
                    "stats": {
                        "observation_count": len(result.observations),
                        "diagnostics": diagnostics,
                    },
                }
            )
            stage_row.status = "SUCCEEDED"
            stage_row.document = stage_run.model_dump(mode="json")
            self.session.commit()
            return True
        # The stage boundary must persist arbitrary Calculator failures as facts.
        except Exception as exc:  # noqa: BLE001
            error_result = MetricResultV1.model_validate(
                {
                    "metric_result_id": uuid7(),
                    "run_id": run.run_id,
                    "stage_run_id": stage_run_id,
                    "dataset_version_id": run.dataset_version_id,
                    "metric": stage.metric.model_dump(mode="json"),
                    "subject": {"scope": "DATASET", "id": str(run.dataset_version_id)},
                    "status": "ERROR",
                    "created_at": datetime.now(UTC),
                }
            )
            self.session.add(
                MetricResultModel(
                    metric_result_id=error_result.metric_result_id,
                    run_id=run.run_id,
                    stage_run_id=stage_run_id,
                    dataset_version_id=run.dataset_version_id,
                    metric_key=stage.metric.key,
                    metric_version=stage.metric.version,
                    status="ERROR",
                    document=error_result.model_dump(mode="json"),
                )
            )
            error = {"type": type(exc).__name__, "message": str(exc)}
            if isinstance(exc, CalculatorError):
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
            self.session.commit()
            return False

    def _calculate(self, run, stage_run, stage, version):
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
        normalized_ref = version.artifact_refs["normalized"]
        normalized_bytes = self.artifact_store.read(normalized_ref["uri"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "sample_records.jsonl"
            output_dir = root / "output"
            input_path.write_bytes(normalized_bytes)
            context = CalculatorContext(
                run_id=run.run_id,
                stage_run_id=stage_run.stage_run_id,
                dataset_version_id=run.dataset_version_id,
                metric_key=stage.metric.key,
                metric_version=stage.metric.version,
                input_path=input_path,
                output_dir=output_dir,
                input_schema_ref=stage.input_schema_ref,
            )
            output = calculator.execute(context, stage.resolved_parameters)
            evidence = []
            output_artifacts = []
            for item in output.evidence:
                if not item.path.resolve().is_relative_to(output_dir.resolve()):
                    raise ValueError(
                        "Calculator evidence path escapes its output directory"
                    )
                filename = item.path.name
                relative = (
                    f"evaluation-runs/{run.run_id}/{stage_run.stage_run_id}/{filename}"
                )
                staging = self.artifact_store.prepare_write(relative)
                staging.write_bytes(item.path.read_bytes())
                stored = self.artifact_store.commit(relative)
                artifact = {
                    "artifact_id": uuid7(),
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
                        "evidence_id": uuid7(),
                        "kind": item.kind,
                        "schema_ref": item.schema_ref,
                        "artifact": artifact,
                        "subject_scope": item.subject_scope,
                        "summary": {
                            "record_count": item.record_count,
                            "error_count": item.error_count,
                            "attributes": item.attributes,
                        },
                        "redaction": {
                            "contains_raw_content": item.contains_raw_content,
                            "policy": item.redaction_policy,
                        },
                    }
                )
        result = MetricResultV1.model_validate(
            {
                "metric_result_id": uuid7(),
                "run_id": run.run_id,
                "stage_run_id": stage_run.stage_run_id,
                "dataset_version_id": run.dataset_version_id,
                "metric": stage.metric.model_dump(mode="json"),
                "subject": {"scope": "DATASET", "id": str(run.dataset_version_id)},
                "status": "OK",
                "observations": output.observations,
                "population": output.population,
                "semantic_coverage": output.semantic_coverage,
                "evidence": evidence,
                "created_at": datetime.now(UTC),
            }
        )
        return result, output_artifacts, output.diagnostics

    def get(self, run_id: UUID) -> EvaluationRunV1 | None:
        row = self.session.get(EvaluationRunModel, run_id)
        return None if row is None else EvaluationRunV1.model_validate(row.document)

    def list_runs(self) -> list[EvaluationRunV1]:
        rows = self.session.scalars(
            select(EvaluationRunModel).order_by(EvaluationRunModel.created_at.desc())
        ).all()
        return [EvaluationRunV1.model_validate(row.document) for row in rows]

    def list_results(self, run_id: UUID) -> list[MetricResultV1]:
        rows = self.session.scalars(
            select(MetricResultModel).where(MetricResultModel.run_id == run_id)
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
        row = self.session.get(
            EvaluationRunModel, run_id, with_for_update=True
        )
        if row is None:
            return False

        if row.status not in self._DELETABLE_STATUSES:
            raise EvaluationRunNotDeletableError(
                status=row.status,
                message=f"评测任务状态为 {row.status}，未到达终态，无法删除",
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
            delete(QualityReportModel).where(QualityReportModel.run_id == run_id)
        )
        self.session.execute(
            delete(RuleEvaluationModel).where(
                RuleEvaluationModel.run_id == run_id
            )
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
        self.session.delete(row)
        self.session.execute(
            delete(PhysicalEvaluationPlanModel).where(
                PhysicalEvaluationPlanModel.physical_plan_id
                == row.physical_plan_id
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

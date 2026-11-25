"""Persist native rule and issue facts for a sealed Run selection."""

from datetime import UTC, datetime
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from qa_eval.application.evaluation.rule_evaluator import evaluate_native_item
from qa_eval.application.evidence.index import ArtifactRecordIndexService
from qa_eval.application.issues.registry import IssueBuilderRegistry
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.application.services.scoring_consumer import ScoringInputs
from qa_eval.contracts.calculator_results import CalculatorResultV1
from qa_eval.contracts.quality import NativeEvaluationItemV1, NativeRuleEvaluationV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel
from qa_eval.infrastructure.db.models.quality_result import RuleEvaluationModel


class NativeFinalizationBackend:
    def __init__(self, sessions: sessionmaker[Session], store: ArtifactStore):
        self.sessions = sessions
        self.store = store

    @staticmethod
    def _check_selection(inputs: ScoringInputs, row: EvaluationRunModel | None) -> None:
        selection = inputs.run.execution.get("selected_attempts")
        if (
            row is None
            or selection is None
            or row.document.get("execution", {}).get("selected_attempts") != selection
            or any(
                part.stage_run_id is not None
                and selection.get(part.stage_id) != str(part.stage_run_id)
                for part in inputs.components
            )
        ):
            raise ValueError("native finalization requires the sealed Run selection")

    def verify_artifacts(self, inputs: ScoringInputs) -> None:
        with self.sessions() as session:
            self._check_selection(
                inputs, session.get(EvaluationRunModel, inputs.run.run_id)
            )
        indexes = ArtifactRecordIndexService(self.store)
        seen = set()
        for component in inputs.components:
            if not isinstance(component.result, CalculatorResultV1):
                continue
            for ref in component.artifacts:
                if ref.artifact.format != "jsonl":
                    raise ValueError(
                        "native artifact requires an indexed JSONL verification path"
                    )
                spec = EvidenceQueryService._evidence_spec(ref)
                artifact_key = (
                    spec.source_uri,
                    spec.identity,
                    ref.summary.record_count,
                )
                if artifact_key in seen:
                    continue
                metadata = indexes.build(spec, force=True)
                if (
                    metadata["status"] != "READY"
                    or metadata.get("record_count") != ref.summary.record_count
                ):
                    raise ValueError("native artifact index verification is incomplete")
                seen.add(artifact_key)

    def materialize_rules_and_issues(self, inputs: ScoringInputs) -> None:
        items = {
            item.key: item
            for item in inputs.contract.observation_items
            if isinstance(item, NativeEvaluationItemV1)
        }
        builders = IssueBuilderRegistry()
        now = datetime.now(UTC)
        for component in inputs.components:
            item = items.get(component.key.item_key)
            result = component.result
            if item is None or not isinstance(result, CalculatorResultV1):
                continue
            with self.sessions() as session:
                self._check_selection(
                    inputs, session.get(EvaluationRunModel, inputs.run.run_id)
                )
                existing = self._existing_rule(session, result)
            rule_id = uuid5(result.calculator_result_id, f"rule/{item.key}")
            evaluation = existing or evaluate_native_item(
                inputs.contract,
                item,
                inputs.run,
                result,
                id_factory=lambda identity=rule_id: identity,
                evaluated_at=now,
            )
            # Builders read artifacts outside a database transaction.
            candidates = builders.build_native(result, evaluation, now, self.store)
            with self.sessions.begin() as session:
                run = session.get(
                    EvaluationRunModel, inputs.run.run_id, with_for_update=True
                )
                self._check_selection(inputs, run)
                step = (
                    run.document["execution"]
                    .get("unified_finalization", {})
                    .get("step")
                )
                if step == 3:
                    return
                if step not in (1, 2):
                    raise ValueError(
                        "Rule/Issue materialization requires a verified finalization checkpoint"
                    )
                current = self._existing_rule(session, result)
                if current is None:
                    session.add(
                        RuleEvaluationModel(
                            rule_evaluation_id=evaluation.rule_evaluation_id,
                            run_id=inputs.run.run_id,
                            calculator_result_id=result.calculator_result_id,
                            status=evaluation.status,
                            document=evaluation.model_dump(mode="json"),
                        )
                    )
                elif current.rule_evaluation_id != evaluation.rule_evaluation_id:
                    raise ValueError("rule identity changed during native finalization")
            for offset in range(0, len(candidates), 100):
                with self.sessions.begin() as session:
                    run = session.get(
                        EvaluationRunModel, inputs.run.run_id, with_for_update=True
                    )
                    self._check_selection(inputs, run)
                    if (
                        run.document.get("execution", {})
                        .get("unified_finalization", {})
                        .get("step")
                        == 3
                    ):
                        return
                    scoped = [
                        issue.model_copy(
                            update={
                                "dedup_key": f"{result.calculator_result_id}:{issue.dedup_key}",
                            }
                        )
                        for issue in candidates[offset : offset + 100]
                    ]
                    QualityResultService(session, self.store)._upsert_issues(scoped)

    @staticmethod
    def _existing_rule(session, result):
        row = session.scalars(
            select(RuleEvaluationModel).where(
                RuleEvaluationModel.run_id == result.run_id,
                RuleEvaluationModel.calculator_result_id == result.calculator_result_id,
            )
        ).one_or_none()
        return NativeRuleEvaluationV1.model_validate(row.document) if row else None

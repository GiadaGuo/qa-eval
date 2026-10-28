from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.logical_planner_service import (
    LogicalPlannerService,
    LogicalPlanningError,
)
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel


@pytest.fixture
def planning_context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        dataset_id, version_id = uuid4(), uuid4()
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
        version = DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{'a' * 64}",
            artifact_refs={
                "manifest": {"uri": "manifest.json"},
                "normalized": {"uri": "samples.jsonl"},
                "field_mapping": {
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {
                                "kind": "CONTENT",
                                "role": "article.body.plain",
                                "modality": "text",
                            },
                        }
                    ]
                },
            },
        )
        session.add(version)
        session.flush()
        MetricRegistryService(session).sync()
        yield session, version


def _rule(operator="GTE"):
    return {
        "operator": operator,
        "threshold": 0.5,
        "severity": "ERROR",
        "on_missing": "UNKNOWN",
    }


def test_single_and_multi_metric_contracts_compile(planning_context):
    session, version = planning_context
    service = QualityContractService(session)
    single = service.publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "single",
                "version_label": "v1",
                "dataset_version_id": version.id,
                "metric_key": "0204",
                "metric_version": "1.0.0",
                "measurement": "complete_record_ratio",
                "calculator_key": "structural_completeness",
                "calculator_version": "1.0.0",
                "parameters": {"content_role": "article.body.plain"},
                "rule": _rule(),
            }
        )
    ).contract
    single_plan = LogicalPlannerService(session).compile(single, version)
    assert [item.metric.key for item in single_plan.operations] == ["0204"]
    assert single_plan.output_bindings[0].contract_item_key == single.items[0].key

    multi = service.publish_multi(
        QualityContractMultiPublishRequest.model_validate(
            {
                "name": "multi",
                "version_label": "v1",
                "dataset_version_id": version.id,
                "items": [
                    {
                        "key": "complete",
                        "metric_key": "0204",
                        "metric_version": "1.0.0",
                        "measurement": "complete_record_ratio",
                        "calculator_key": "structural_completeness",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": _rule(),
                    },
                    {
                        "key": "duplicate",
                        "metric_key": "02080103",
                        "metric_version": "1.0.0",
                        "measurement": "exact_affected_record_ratio",
                        "calculator_key": "exact_text_duplicate",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": _rule("LTE"),
                    },
                ],
            }
        )
    ).contract
    multi_plan = LogicalPlannerService(session).compile(multi, version)
    assert {item.metric.key for item in multi_plan.operations} == {"0204", "02080103"}
    assert len(multi_plan.output_bindings) == 2


def _semantic_contract(
    version_id, *, model="gpt-semantic", prompt_family="text-quality-semantic-v1"
):
    metrics = [
        (
            "density",
            "02080102",
            "mean_effective_knowledge_score",
            "knowledge_information_density",
        ),
        (
            "missing",
            "02080105",
            "key_element_missing_ratio",
            "information_missing_degree",
        ),
        ("coherence", "02080107", "coherent_text_ratio", "text_coherence"),
    ]
    items = []
    for key, metric, measurement, calculator in metrics:
        sharing = {
            "allow_shared_execution": True,
            "provider": "test-provider",
            "model": model,
            "sampling_fingerprint": "all-records",
            "prompt_family": prompt_family,
            "rubric_bundle": "text-quality-v1",
            "preprocessing_fingerprint": "plain-v1",
            "context_policy": "16k",
            "privacy_policy": "normalized-only",
            "input_selector": "article.body.plain",
            "independent_outputs": True,
        }
        items.append(
            {
                "key": key,
                "metric": {
                    "key": metric,
                    "version": "1.0.0",
                    "measurement": measurement,
                },
                "scope": "DATASET",
                "calculation": {
                    "parameters": {"sharing": sharing},
                    "implementation_constraints": {
                        "deterministic": False,
                        "attributes": {
                            "calculator_key": calculator,
                            "calculator_version": "1.0.0",
                        },
                    },
                },
                "rule": {"operator": "GTE", "threshold": 0.5},
                "severity": "ERROR",
                "on_missing": "UNKNOWN",
            }
        )
    return QualityContractVersionV1.model_validate(
        {
            "contract_id": uuid4(),
            "contract_version_id": uuid4(),
            "version_label": "v1",
            "name": "semantic",
            "status": "PUBLISHED",
            "applicability": {"modalities": ["text"], "task_types": []},
            "items": items,
            "gate": {"unknown_policy": "FAIL", "fail_on_severities": ["ERROR"]},
            "fingerprint": f"sha256:{'b' * 64}",
            "published_at": datetime.now(UTC),
        }
    )


def _enable_semantic_descriptors(session):
    keys = {
        "knowledge_information_density",
        "information_missing_degree",
        "text_coherence",
    }
    rows = session.scalars(
        select(CalculatorImplementationModel).where(
            CalculatorImplementationModel.calculator_key.in_(keys)
        )
    ).all()
    for row in rows:
        row.document = {
            **row.document,
            "entrypoint": "qa_eval.calculators.structural_completeness:StructuralCompletenessCalculator",
            "code_digest": f"sha256:{'c' * 64}",
            "availability": {"status": "AVAILABLE", "reason": None},
            "input_contract": {
                "sample_record_schema": "2.0",
                "role_selectors": ["article.body.plain"],
            },
            "parameter_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sharing"],
                "properties": {"sharing": {"type": "object"}},
            },
        }
    session.flush()


def test_compatible_semantic_metrics_share_one_operation_and_three_outputs(
    planning_context,
):
    session, version = planning_context
    _enable_semantic_descriptors(session)
    plan = LogicalPlannerService(session).compile(
        _semantic_contract(version.id), version
    )
    assert len(plan.shared_computation_groups) == 1
    assert len(plan.shared_computation_groups[0].metric_keys) == 3
    assert len(plan.output_bindings) == 3
    assert len({item.shared_computation_id for item in plan.output_bindings}) == 1


def test_incompatible_semantic_metrics_are_not_shared(planning_context):
    session, version = planning_context
    _enable_semantic_descriptors(session)
    contract = _semantic_contract(version.id)
    changed = contract.model_dump(mode="json")
    changed["items"][2]["calculation"]["parameters"]["sharing"]["model"] = "other-model"
    plan = LogicalPlannerService(session).compile(
        QualityContractVersionV1.model_validate(changed), version
    )
    assert len(plan.shared_computation_groups) == 1
    assert set(plan.shared_computation_groups[0].metric_keys) == {
        "02080102",
        "02080105",
    }
    coherence = next(
        item for item in plan.output_bindings if item.metric.key == "02080107"
    )
    assert coherence.shared_computation_id is None


def test_semantic_calculators_are_executable_and_missing_role_is_diagnostic(planning_context):
    session, version = planning_context
    plan = LogicalPlannerService(session).compile(_semantic_contract(version.id), version)
    assert len(plan.shared_computation_groups) == 1

    version.artifact_refs["field_mapping"] = {"mappings": []}
    session.flush()
    # Build directly because Contract publication correctly blocks inapplicable input.
    contract = _semantic_contract(version.id)
    with pytest.raises(LogicalPlanningError) as missing:
        LogicalPlannerService(session).compile(contract, version)
    assert "REQUIRED_ROLE_MISSING" in {item.code for item in missing.value.diagnostics}


def test_cycle_and_unknown_dependencies_are_rejected():
    with pytest.raises(LogicalPlanningError, match="DEPENDENCY_CYCLE"):
        LogicalPlannerService.validate_dag(["a", "b"], [("a", "b"), ("b", "a")])
    with pytest.raises(LogicalPlanningError, match="UNKNOWN_DEPENDENCY"):
        LogicalPlannerService.validate_dag(["a"], [("missing", "a")])

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session
from test_quality_result_api import _fk_enforcing_engine, _seed_contract

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.logical_planner_service import semantic_fingerprint
from qa_eval.application.services.scoring_consumer import (
    ScoringConsumer,
    ScoringContractFixture,
)
from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.evaluation_run import (
    CalculatorResultArtifactModel,
    CalculatorResultModel,
    EvaluationRunModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


@pytest.fixture
def persisted(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        seed = _seed_contract(session, store)
        session.commit()
        yield session, store, seed
    engine.dispose()


def test_historical_worker_and_legacy_rule_read(persisted):
    session, store, seed = persisted
    service = EvaluationRunService(session, store)
    run = service.create(seed)
    session.commit()
    assert service.execute(run.run_id).status == "SUCCEEDED"
    selected = {
        row.stage_id: row.stage_run_id for row in service.list_stage_runs(run.run_id)
    }
    consumer = ScoringConsumer(session, EvidenceQueryService(session, store))
    inputs = consumer.read(run.run_id, selected_attempts=selected)
    assert inputs.components
    for component in inputs.components:
        assert component.status == "PARTIAL", component.reasons
        assert component.source == "LEGACY"
        assert "LEGACY_DECISION_PROOF_UNAVAILABLE" in component.reasons
        assert consumer.rules(inputs, component.key)
        consumer.issues(inputs, component.key)
    assert not inputs.formal_ready


COUNT_KEYS = {
    "population": "input_count",
    "passed": "pass_count",
    "failed": "affected_record_count",
    "unknown": "unknown_count",
    "not_applicable": "na_count",
}


def _count_schema():
    return {
        "schema_version": "1.0",
        "decision_retention": "ALL",
        "decision_population_key": "input_count",
        "statistics": [
            {
                "key": key,
                "kind": "count",
                "unit": "record",
                "population": "all",
                "method": "fixture",
            }
            for key in COUNT_KEYS.values()
        ]
        + [
            {
                "key": "coverage",
                "kind": "ratio",
                "unit": "ratio",
                "population": "all",
                "method": "fixture",
                "numerator_key": "pass_count",
                "denominator_key": "input_count",
            }
        ],
    }


def _metric(key):
    return {"key": key, "version": "2.0.0"}


def _binding(slot, metric):
    return {
        "contract_item_key": slot,
        "output_slot_id": slot,
        "metric": metric,
        "output_kind": "CALCULATOR_RESULT",
        "projections": [],
        "native_output": _count_schema(),
    }


def _add_attempt(session, store, run, plan, stage, attempt, statuses):
    stage_run_id = uuid4()
    fingerprint = run.document["input_fingerprint"]
    stage_document = {
        "stage_run_id": str(stage_run_id),
        "run_id": str(run.run_id),
        "physical_plan_id": str(plan.physical_plan_id),
        "stage_id": stage["stage_id"],
        "attempt": attempt,
        "status": "SUCCEEDED",
        "calculator": stage["calculator"],
        "resolved_parameters": {},
        "parameters_fingerprint": semantic_fingerprint({}),
        "input_fingerprint": fingerprint,
    }
    stage_row = StageRunModel(
        stage_run_id=stage_run_id,
        run_id=run.run_id,
        physical_plan_id=plan.physical_plan_id,
        stage_id=stage["stage_id"],
        attempt=attempt,
        status="SUCCEEDED",
    )
    slot = stage["output_bindings"][0]["output_slot_id"]
    refs = []
    for kind, schema, documents in (
        (
            "DECISION",
            DECISION_SCHEMA_REF,
            [
                {
                    "row_id": f"d{i}",
                    "sample_id": f"r{i}",
                    "rule_id": "fixture",
                    "target": "SELF",
                    "status": status,
                    "reason": "fixture",
                    "evidence_refs": [{"artifact_key": "events", "row_id": "event"}]
                    if status == "FAIL"
                    else [],
                }
                for i, status in enumerate(statuses)
            ],
        ),
        ("EVIDENCE", "fixture://evidence", [{"row_id": "event"}]),
    ):
        filename = f"{slot}/{attempt}/{kind}.jsonl"
        payload = "".join(json.dumps(item) + "\n" for item in documents).encode()
        store.prepare_write(filename).write_bytes(payload)
        stored = store.commit(filename)
        refs.append(
            {
                "evidence_id": str(uuid4()),
                "kind": kind,
                "schema_ref": schema,
                "subject_scope": "SAMPLE",
                "artifact": {
                    "artifact_id": str(uuid4()),
                    "kind": "METRIC_EVIDENCE",
                    "uri": stored.uri,
                    "format": "jsonl",
                    "media_type": "application/x-ndjson",
                    "schema_ref": schema,
                    "size_bytes": len(payload),
                    "record_count": len(documents),
                    "checksum": {"algorithm": "sha256", "value": stored.sha256},
                },
                "summary": {
                    "record_count": len(documents),
                    "error_count": 0,
                    "attributes": {"artifact_key": "events"}
                    if kind == "EVIDENCE"
                    else {},
                },
                "redaction": {"contains_raw_content": False, "policy": "fixture"},
            }
        )
    stage_row.document = {
        **stage_document,
        "output_artifacts": [ref["artifact"] for ref in refs],
    }
    session.add(stage_row)
    session.flush()
    result_id = uuid4()
    counts = dict(
        zip(
            COUNT_KEYS.values(),
            [
                len(statuses),
                statuses.count("PASS"),
                statuses.count("FAIL"),
                statuses.count("UNKNOWN"),
                statuses.count("NOT_APPLICABLE"),
            ],
            strict=True,
        )
    )
    result = CalculatorResultModel(
        calculator_result_id=result_id,
        run_id=run.run_id,
        stage_run_id=stage_run_id,
        contract_item_key=slot,
        output_slot_id=slot,
        document={
            "calculator_result_id": str(result_id),
            "run_id": str(run.run_id),
            "stage_run_id": str(stage_run_id),
            "dataset_version_id": str(run.dataset_version_id),
            "contract_item_key": slot,
            "output_slot_id": slot,
            "metric": stage["metric"],
            "calculator_key": stage["calculator"]["key"],
            "calculator_version": "1.0.0",
            "input_fingerprint": fingerprint,
            "plan_fingerprint": plan.semantic_fingerprint,
            "status": "OK",
            "output_schema": _count_schema(),
            "statistics": {
                **counts,
                "coverage": counts["pass_count"] / len(statuses) if statuses else None,
            },
            "decision_artifact_refs": refs[:1],
            "evidence_artifact_refs": refs[1:],
        },
    )
    session.add(result)
    session.flush()
    session.add_all(
        [
            CalculatorResultArtifactModel(
                evidence_id=UUID(ref["evidence_id"]), calculator_result_id=result_id
            )
            for ref in refs
        ]
    )
    session.flush()
    evidence = EvidenceQueryService(session, store)
    for ref in refs:
        evidence.build_evidence_index(UUID(ref["evidence_id"]))
    return stage_run_id, result


@pytest.fixture
def native(persisted):
    session, store, seed = persisted
    contract = session.get(QualityContractVersionModel, seed)
    run_id, plan_id = uuid4(), uuid4()
    now = datetime.now(UTC).isoformat()
    metric = _metric("0202")
    slots = ("dictionary", "regex")
    contract.document = {
        **contract.document,
        "schema_version": "1.1",
        "items": [],
        "observation_items": [
            {
                "schema_version": "1.0",
                "strategy": "OBSERVE_ONLY",
                "scope": "DATASET",
                "key": slot,
                "metric": metric,
                "calculation": {},
                "projections": [],
            }
            for slot in slots
        ],
    }
    stages = [
        {
            "stage_id": slot,
            "order": order,
            "metric": metric,
            "calculator": {
                "key": slot,
                "version": "1.0.0",
            },
            "resolved_parameters": {},
            "input_schema_ref": "fixture",
            "output_schema_ref": "fixture",
            "output_bindings": [_binding(slot, metric)],
        }
        for order, slot in enumerate(slots)
    ]
    semantic = {
        "dataset_version_id": str(contract.target_dataset_version_id),
        "contract_version_id": str(seed),
        "stages": stages,
    }
    fingerprint = semantic_fingerprint(semantic)
    plan_row = PhysicalEvaluationPlanModel(
        physical_plan_id=plan_id,
        dataset_version_id=contract.target_dataset_version_id,
        contract_version_id=seed,
        semantic_fingerprint=fingerprint,
        document={
            **semantic,
            "physical_plan_id": str(plan_id),
            "semantic_fingerprint": fingerprint,
            "created_at": now,
        },
    )
    session.add(plan_row)
    session.flush()
    run_row = EvaluationRunModel(
        run_id=run_id,
        physical_plan_id=plan_id,
        dataset_version_id=contract.target_dataset_version_id,
        contract_version_id=seed,
        status="SUCCEEDED",
        document={
            "run_id": str(run_id),
            "physical_plan_id": str(plan_id),
            "dataset_version_id": str(contract.target_dataset_version_id),
            "contract_version_id": str(seed),
            "status": "SUCCEEDED",
            "input_fingerprint": semantic_fingerprint("fixture-input"),
            "created_at": now,
        },
    )
    session.add(run_row)
    session.flush()
    first, dictionary = _add_attempt(
        session,
        store,
        run_row,
        plan_row,
        stages[0],
        1,
        ["PASS"] * 8 + ["FAIL", "UNKNOWN"],
    )
    retry, _ = _add_attempt(
        session, store, run_row, plan_row, stages[0], 2, ["UNKNOWN"]
    )
    second, regex = _add_attempt(
        session, store, run_row, plan_row, stages[1], 1, ["PASS"] * 9 + ["FAIL"]
    )
    fixture = ScoringContractFixture.model_validate(
        {
            "source": "FIXTURE",
            "run_id": run_id,
            "plan_fingerprint": fingerprint,
            "contract_fingerprint": contract.fingerprint,
            "mode": "BASIC",
            "nodes": [
                {"metric": _metric(f"020{i}"), "dimension": "DATA_QUALITY"}
                for i in range(1, 9)
            ]
            + [
                {"metric": _metric(f"010{i}"), "dimension": "DOCUMENTATION"}
                for i in range(1, 5)
            ],
            "slots": {
                slot: {
                    "grain": "RECORD",
                    "mapping_id": "records",
                    "mapping_version": "1",
                    "statistic_keys": list(COUNT_KEYS.values()),
                    "count_keys": COUNT_KEYS,
                }
                for slot in slots
            },
        }
    )
    session.commit()
    return (
        ScoringConsumer(session, EvidenceQueryService(session, store)),
        fixture,
        {"dictionary": first, "regex": second},
        retry,
        dictionary,
        regex,
    )


def test_frozen_0202_same_statistic_and_evidence(native):
    consumer, fixture, selected, _, dictionary, _ = native
    inputs = consumer.read(fixture.run_id, selected_attempts=selected, fixture=fixture)
    left, right = inputs.components
    assert (left.status, right.status) == ("PARTIAL", "OK"), (
        left.reasons,
        right.reasons,
    )
    assert left.stage_run_id == selected["dictionary"]
    assert left.counts.coverage_basis == (9, 10)
    assert right.counts.coverage_basis == (10, 10)
    assert [
        inputs.statistic(item.key, "affected_record_count") for item in (left, right)
    ] == [1, 1]
    assert left.key != right.key
    assert {item.key for item in inputs.missing_metrics} == {
        "0201",
        "0203",
        "0204",
        "0205",
        "0206",
        "0207",
        "0208",
    }
    ref = left.result.evidence_artifact_refs[0]
    assert consumer.evidence_row(left.result, ref.evidence_id, "event") == {
        "row_id": "event"
    }
    assert left.result.calculator_result_id == dictionary.calculator_result_id
    assert not inputs.formal_ready


def test_zero_denominator_and_missing_attempt(native):
    consumer, fixture, _selected, retry, _, _ = native
    inputs = consumer.read(
        fixture.run_id,
        selected_attempts={"dictionary": retry},
        fixture=fixture,
    )
    left, right = inputs.components
    assert left.status == "PARTIAL"
    assert left.counts.coverage_basis == (0, 1)
    assert "ZERO_SCORE_DENOMINATOR" in left.reasons
    assert right.status == "MISSING"
    assert right.stage_run_id is None


@pytest.mark.parametrize(
    "damage", ["closure", "ratio", "fingerprint", "run", "reference"]
)
def test_invalid_native_result(native, damage):
    consumer, fixture, selected, _, dictionary, _ = native
    doc = dict(dictionary.document)
    if damage in {"closure", "ratio"}:
        doc["statistics"] = {
            **doc["statistics"],
            ("unknown_count" if damage == "closure" else "coverage"): 0,
        }
    elif damage in {"fingerprint", "run"}:
        doc["plan_fingerprint" if damage == "fingerprint" else "run_id"] = (
            semantic_fingerprint("wrong") if damage == "fingerprint" else str(uuid4())
        )
    else:
        doc["evidence_artifact_refs"] = [
            {**ref, "evidence_id": str(uuid4())}
            for ref in doc["evidence_artifact_refs"]
        ]
    dictionary.document = doc
    consumer.session.flush()
    inputs = consumer.read(fixture.run_id, selected_attempts=selected, fixture=fixture)
    assert inputs.components[0].status == "ERROR"
    assert inputs.components[1].status == "OK"


def test_applicability_requires_frozen_basis(native):
    consumer, fixture, selected, _, dictionary, _ = native
    dictionary.document = {
        **dictionary.document,
        "status": "NOT_APPLICABLE",
        "statistics": {},
    }
    consumer.session.flush()
    read = lambda: consumer.read(
        fixture.run_id, selected_attempts=selected, fixture=fixture
    )
    assert read().components[0].status == "ERROR"
    config = fixture.slots["dictionary"]
    config.applicability = "NOT_APPLICABLE"
    assert read().components[0].status == "ERROR"
    config.applicability_reason = "fixture applicability rule"
    assert read().components[0].status == "NA"


def test_duplicate_slot_and_0208_guard(native):
    consumer, fixture, selected, _, _, _ = native
    _, plan, run = consumer._read_frozen(fixture.run_id)
    row = consumer.session.get(PhysicalEvaluationPlanModel, plan.physical_plan_id)
    stages = row.document["stages"]
    duplicate = {**stages[1], "output_bindings": stages[0]["output_bindings"]}
    semantic = {
        "dataset_version_id": str(run.dataset_version_id),
        "contract_version_id": str(run.contract_version_id),
        "stages": [stages[0], duplicate],
    }
    row.semantic_fingerprint = semantic_fingerprint(semantic)
    row.document = {
        **row.document,
        **semantic,
        "semantic_fingerprint": row.semantic_fingerprint,
    }
    with pytest.raises(ValueError, match="duplicate"):
        consumer.read(fixture.run_id, selected_attempts=selected)
    contract = consumer.session.get(
        QualityContractVersionModel, run.contract_version_id
    )
    observations = contract.document["observation_items"]
    contract.document = {
        **contract.document,
        "observation_items": [
            *observations,
            *[
                {**observations[0], "key": key, "metric": _metric(key)}
                for key in ("0208", "02080103")
            ],
        ],
    }
    row.semantic_fingerprint = plan.semantic_fingerprint
    row.document = {
        **row.document,
        "stages": stages,
        "semantic_fingerprint": plan.semantic_fingerprint,
    }
    with pytest.raises(ValueError, match="mutually exclusive"):
        consumer.read(fixture.run_id, selected_attempts=selected)


def test_current_native_worker_read(persisted, tmp_path, monkeypatch):
    from test_native_calculator_results import LEGACY, OBSERVATION, register_fixture
    from test_quality_result_api import _client

    session, store, seed = persisted
    register_fixture(session, tmp_path, monkeypatch)
    version_id = session.get(
        QualityContractVersionModel, seed
    ).target_dataset_version_id
    session.commit()
    with _client(session.get_bind(), store) as client:
        response = client.post(
            "/api/v1/quality-contract-versions/multi",
            json={
                "name": "native producer compatibility",
                "version_label": "v1",
                "dataset_version_id": str(version_id),
                "items": [LEGACY],
                "observation_items": [{**OBSERVATION, "key": "current-native"}],
            },
        )
        assert response.status_code == 201, response.text
    contract_id = UUID(response.json()["contract"]["contract_version_id"])
    service = EvaluationRunService(session, store)
    run = service.create(contract_id)
    session.commit()
    assert service.execute(run.run_id).status == "SUCCEEDED"
    selected = {
        row.stage_id: row.stage_run_id for row in service.list_stage_runs(run.run_id)
    }
    evidence = EvidenceQueryService(session, store)
    consumer = ScoringConsumer(session, evidence)
    inputs = consumer.read(run.run_id, selected_attempts=selected)
    native = next(item for item in inputs.components if item.source == "NATIVE")
    assert native.status == "PARTIAL", native.reasons
    assert native.reasons == ("FROZEN_SCORING_CONFIG_MISSING",)
    assert inputs.statistic(native.key, "observed") == 1
    (ref,) = native.artifacts
    evidence.build_evidence_index(ref.evidence_id)
    assert (
        consumer.evidence_row(native.result, ref.evidence_id, "a")["status"]
        == "OBSERVED"
    )
    assert not inputs.formal_ready


def test_unplanned_item_same_metric_stays_missing(native):
    consumer, fixture, selected, _, _, _ = native
    _, _, run = consumer._read_frozen(fixture.run_id)
    contract = consumer.session.get(
        QualityContractVersionModel, run.contract_version_id
    )
    items = contract.document["observation_items"]
    contract.document = {
        **contract.document,
        "observation_items": [
            *items,
            {**items[0], "key": "unplanned"},
        ],
    }
    inputs = consumer.read(fixture.run_id, selected_attempts=selected, fixture=fixture)
    unplanned = next(
        item for item in inputs.components if item.key.item_key == "unplanned"
    )
    assert unplanned.status == "MISSING"
    assert unplanned.reasons == ("CONTRACT_ITEM_NOT_PLANNED",)


def test_native_rules_and_issues_are_scoped_to_selected_result(native):
    from qa_eval.infrastructure.db.models.quality_result import (
        QualityIssueModel,
        RuleEvaluationModel,
    )

    consumer, fixture, selected, _, dictionary, _ = native
    inputs = consumer.read(fixture.run_id, selected_attempts=selected, fixture=fixture)
    left, right = inputs.components
    rule_id, issue_id = uuid4(), uuid4()
    now = datetime.now(UTC).isoformat()
    result = left.result
    ref = {
        "contract_item_key": left.key.item_key,
        "output_slot_id": left.key.output_slot_id,
        "calculator_version": result.calculator_version,
        "statistics_key": "affected_record_count",
    }
    common = {
        "run_id": str(inputs.run.run_id),
        "dataset_version_id": str(inputs.run.dataset_version_id),
    }
    calculator = {"key": result.calculator_key, "version": result.calculator_version}
    statistic = next(
        item
        for item in result.output_schema.statistics
        if item.key == ref["statistics_key"]
    )
    consumer.session.add(
        RuleEvaluationModel(
            rule_evaluation_id=rule_id,
            run_id=inputs.run.run_id,
            calculator_result_id=dictionary.calculator_result_id,
            status="FAIL",
            document={
                **common,
                "schema_version": "1.1",
                "rule_evaluation_id": str(rule_id),
                "contract_version_id": str(inputs.contract.contract_version_id),
                "calculator_result_id": str(dictionary.calculator_result_id),
                "calculator": calculator,
                "metric": result.metric.model_dump(mode="json"),
                "statistic_ref": ref,
                "statistic": statistic.model_dump(mode="json"),
                "severity": "WARNING",
                "rule": {"operator": "LTE", "expected": 0},
                "status": "FAIL",
                "reason_code": "THRESHOLD_VIOLATED",
                "metric_value": 1,
                "evaluated_at": now,
            },
        )
    )
    consumer.session.add(
        QualityIssueModel(
            issue_id=issue_id,
            run_id=inputs.run.run_id,
            calculator_result_id=dictionary.calculator_result_id,
            determination="CONFIRMED",
            severity="WARNING",
            code="FIXTURE",
            dedup_key="dictionary-r8",
            document={
                **common,
                "issue_id": str(issue_id),
                "detector": calculator,
                "category": "fixture",
                "code": "FIXTURE",
                "determination": "CONFIRMED",
                "severity": "WARNING",
                "scope": "SAMPLE",
                "subject": {"type": "SAMPLE", "sample_id": "r8"},
                "message": "fixture",
                "dedup_key": "dictionary-r8",
                "status": "OPEN",
                "created_at": now,
                "updated_at": now,
                "source": {
                    "calculator_result_id": str(dictionary.calculator_result_id),
                    "metric_key": "0202",
                    "metric_version": result.metric.version,
                    "statistic_ref": ref,
                    "rule_evaluation_id": str(rule_id),
                    "decision_rule_id": "fixture",
                    "decision_status": "FAIL",
                },
                "evidence": [
                    {
                        "evidence_id": str(
                            result.evidence_artifact_refs[0].evidence_id
                        ),
                        "locator": {"row_id": "event"},
                    }
                ],
            },
        )
    )
    consumer.session.flush()
    assert [item.rule_evaluation_id for item in consumer.rules(inputs, left.key)] == [
        rule_id
    ]
    assert [item.issue_id for item in consumer.issues(inputs, left.key)] == [issue_id]
    assert consumer.rules(inputs, right.key) == ()
    assert consumer.issues(inputs, right.key) == ()

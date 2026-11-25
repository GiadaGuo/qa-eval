import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session
from test_quality_result_api import _client, _fk_enforcing_engine, _seed_contract

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import EvidenceOutput, NativeCalculatorOutput
from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF
from qa_eval.contracts.execution import PhysicalEvaluationPlanV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import PhysicalEvaluationPlanModel
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

ROOT = Path(__file__).resolve().parents[2]
OBSERVATION = {
    "schema_version": "1.0",
    "strategy": "OBSERVE_ONLY",
    "projections": [],
    "metric_key": "0205",
    "metric_version": "1.0.0",
    "calculator_key": "observation_fixture",
    "calculator_version": "1.0.0",
}
LEGACY = {
    "key": "traceability",
    "metric_key": "0205",
    "metric_version": "1.0.0",
    "measurement": "traceable_record_ratio",
    "calculator_key": "content_authenticity",
    "calculator_version": "1.0.0",
    "rule": {"strategy": "OBSERVE_ONLY", "severity": "INFO", "on_missing": "UNKNOWN"},
}


class ObservationFixture:
    calculator_key = "observation_fixture"
    version = "1.0.0"
    fail = False

    def source_paths(self):
        return (ROOT / "src" / "qa_eval" / "calculators" / "protocol.py",)

    def validate(self, context, parameters):
        return []

    def execute(self, context, parameters):
        if self.fail:
            raise RuntimeError("fixture failure")
        context.output_dir.mkdir()
        path = context.output_dir / "decisions.jsonl"
        path.write_text(
            json.dumps(
                {
                    "row_id": "a",
                    "sample_id": "a",
                    "rule_id": "fixture",
                    "status": "OBSERVED",
                    "target": "SELF",
                    "reason": "fixture",
                    "locations": [
                        {
                            "content_id": "body",
                            "coordinate_space": "CONTENT_TEXT_UNICODE_CODE_POINT",
                            "start": 0,
                            "end": 1,
                        }
                    ],
                }
            )
            + "\n"
        )
        return NativeCalculatorOutput(
            {
                "input_count": 3,
                "observed": 1,
                "other": 2,
                "rate": 1 / 3,
                "states": {"OBSERVED": 1},
            },
            decisions=EvidenceOutput(path, "DECISIONS", DECISION_SCHEMA_REF, 1),
        )


def register_fixture(session, tmp_path, monkeypatch):
    import qa_eval.calculators.protocol as module

    monkeypatch.setattr(module, "ObservationFixture", ObservationFixture, raising=False)
    registry = tmp_path / "registry"
    for directory in ("metrics", "calculators", "bindings"):
        (registry / directory).mkdir(parents=True)
        for path in (ROOT / "configs" / "registry" / directory).glob("*.json"):
            (registry / directory / path.name).write_bytes(path.read_bytes())
    descriptor = json.loads(
        (registry / "calculators" / "content-authenticity-v1.0.0.json").read_text()
    )
    descriptor.update(
        calculator_key="observation_fixture",
        deterministic=True,
        entrypoint="qa_eval.calculators.protocol:ObservationFixture",
        code_digest=TrustedCalculatorLoader.code_digest(ObservationFixture()),
        output_schema_ref="qa-eval://schemas/calculator-result/1.0.schema.json",
        native_output={
            "schema_version": "1.0",
            "decision_retention": "EXCEPTIONS",
            "decision_population_key": "input_count",
            "statistics": [
                {
                    "key": key,
                    "kind": kind,
                    "unit": unit,
                    "population": "input",
                    "method": "fixture",
                    **extra,
                }
                for key, kind, unit, extra in [
                    ("input_count", "count", "records", {}),
                    ("observed", "count", "records", {"partition_of": "input_count"}),
                    ("other", "count", "records", {"partition_of": "input_count"}),
                    (
                        "rate",
                        "ratio",
                        "ratio",
                        {"numerator_key": "observed", "denominator_key": "input_count"},
                    ),
                    ("states", "distribution", "records", {}),
                ]
            ],
        },
    )
    (registry / "calculators" / "observation.json").write_text(json.dumps(descriptor))
    (registry / "bindings" / "observation.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "metric_key": "0205",
                "metric_version": "1.0.0",
                "calculator_key": "observation_fixture",
                "calculator_version": "1.0.0",
                "priority": 100,
                "enabled": True,
                "projections": [],
            }
        )
    )
    MetricRegistryService(session, registry).sync()


def test_native_run_contract_api_evidence_and_retry(tmp_path, monkeypatch):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        seed = _seed_contract(session, store)
        version_id = session.get(
            QualityContractVersionModel, seed
        ).target_dataset_version_id
        register_fixture(session, tmp_path, monkeypatch)
        session.commit()
    payload = {
        "name": "0205",
        "version_label": "v1",
        "dataset_version_id": str(version_id),
        "items": [LEGACY],
    }
    with _client(engine, store) as client:
        path = "/api/v1/quality-contract-versions/multi"
        legacy = client.post(path, json=payload)
        assert legacy.status_code == 201, legacy.text
        observations = [{**OBSERVATION, "key": key} for key in ("first", "second")]
        added = client.post(path, json={**payload, "observation_items": observations})
        assert added.status_code == 201, added.text
        assert added.json()["contract"]["schema_version"] == "1.1"
        invalid = client.post(
            path,
            json={
                **payload,
                "items": [{**LEGACY, "calculator_key": OBSERVATION["calculator_key"]}],
            },
        )
        assert invalid.status_code == 422
    with Session(engine) as session:
        service = EvaluationRunService(session, store)
        baseline = service.create(
            UUID(legacy.json()["contract"]["contract_version_id"])
        )
        run = service.create(UUID(added.json()["contract"]["contract_version_id"]))
        session.commit()
        assert service.execute(baseline.run_id).status == "SUCCEEDED"
        monkeypatch.setattr(ObservationFixture, "fail", True)
        assert service.execute(run.run_id).status == "PARTIAL_SUCCEEDED"
        plan_row = session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        plan = PhysicalEvaluationPlanV1.model_validate(plan_row.document)
        assert len(plan.stages) == 2
        stage = plan.stages[1]
        assert [item["output_slot_id"] for item in stage.output_bindings] == [
            "first",
            "second",
        ]
        failed = service.list_calculator_results(run.run_id)
        assert {value.status for value in failed} == {"ERROR"}
        assert len({value.stage_run_id for value in failed}) == 1
        # Exercise the same StageRun entry point with a resumed running run.
        from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel

        row = session.get(EvaluationRunModel, run.run_id)
        row.document = {**row.document, "status": "RUNNING"}
        row.status = "RUNNING"
        session.commit()
        monkeypatch.setattr(ObservationFixture, "fail", False)
        version = session.get(DatasetVersionModel, version_id)
        assert service._execute_stage(run, stage, version)
        assert service._execute_stage(run, stage, version)
        assert sorted(
            s.attempt
            for s in service.list_stage_runs(run.run_id)
            if s.stage_id == stage.stage_id
        ) == [1, 2]
        assert len(service.list_results(run.run_id)) == 1
    with _client(engine, store) as client:
        prefix = f"/api/v1/evaluation-runs/{run.run_id}"
        results = client.get(f"{prefix}/calculator-results").json()
        assert (
            len(results) == len({item["calculator_result_id"] for item in results}) == 4
        )
        selected = client.get(
            f"{prefix}/calculator-results", params={"contract_item_key": "first"}
        ).json()
        result = next(item for item in selected if item["status"] == "OK")
        result_id = result["calculator_result_id"]
        assert len(selected) == 2
        assert client.get(f"{prefix}/calculator-results/{result_id}").json() == result
        assert result["statistics"]["input_count"] == 3
        evidence_id = result["decision_artifact_refs"][0]["evidence_id"]
        metadata = client.get(f"/api/v1/evidence/{evidence_id}").json()
        assert metadata["calculator_result_id"] == result_id
        assert not metadata["summary"]["attributes"][
            "retained_decisions_cover_population"
        ]
        assert (
            client.post(f"/api/v1/evidence/{evidence_id}/index/rebuild").status_code
            == 200
        )
        rows = client.get(
            f"/api/v1/evidence/{evidence_id}/rows", params={"sample_id": "a"}
        ).json()["rows"]
        assert len(rows) == 1 and rows[0]["rule_id"] == "fixture"
        base = f"/api/v1/evaluation-runs/{baseline.run_id}"
        report_a = client.get(f"{base}/quality-report").json()
        report_b = client.get(f"{prefix}/quality-report").json()
        for key in ("coverage", "scoring", "issue_summary", "gate_status"):
            assert report_a.get(key) == report_b.get(key)
        assert client.get(f"{prefix}/quality-issues").json()["total"] == 0


def test_native_statistics_validation(tmp_path, monkeypatch):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        register_fixture(session, tmp_path, monkeypatch)
        schema = (
            MetricRegistryService(session)
            .get_calculator("observation_fixture", "1.0.0")
            .native_output
        )
    empty = {"input_count": 0, "observed": 0, "other": 0, "rate": None, "states": {}}
    schema.validate_statistics(empty)
    for corrupt in (
        {"rate": 0},
        {"other": 1},
        {"observed": -1},
        {"states": {"bad": -1}},
    ):
        with pytest.raises(ValueError):
            schema.validate_statistics({**empty, **corrupt})
    with pytest.raises(ValueError, match="duplicate statistic"):
        type(schema).model_validate(
            {**schema.model_dump(), "statistics": [schema.statistics[0]] * 2}
        )


def test_native_migration_preserves_existing_evaluation(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from qa_eval.config import get_settings
    from qa_eval.infrastructure.db.session import create_database_engine

    database = tmp_path / "existing.sqlite3"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("QA_EVAL_DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_database_engine(url)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
        with Session(engine) as session:
            contract_id = _seed_contract(session, store)
            service = EvaluationRunService(session, store)
            run = service.create(contract_id)
            session.commit()
            assert service.execute(run.run_id).status == "SUCCEEDED"

        def snapshot():
            with sqlite3.connect(database) as connection:
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return {
                    table: connection.execute(
                        f"SELECT {columns} FROM {table} ORDER BY 1"
                    ).fetchall()
                    for table in (
                        "datasets",
                        "dataset_versions",
                        "quality_contract_versions",
                        "evaluation_runs",
                        "stage_runs",
                        "metric_results",
                        "rule_evaluations",
                        "quality_issues",
                        "quality_reports",
                    )
                    for columns in (
                        "id, name, description, modality, created_at, updated_at"
                        if table == "datasets" else "*",
                    )
                }

        before = snapshot()
        command.downgrade(config, "9e5a2b3c4d6e")
        assert before["metric_results"] and before["quality_reports"]
        command.upgrade(config, "head")
        assert snapshot() == before
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT enabled, source_kind, version_sequence FROM datasets"
            ).fetchone() == (1, None, 1)
        with Session(engine) as session:
            service = EvaluationRunService(session, store)
            assert service.list_calculator_results(run.run_id) == []
            assert len(service.list_results(run.run_id)) == 1
    finally:
        engine.dispose()
        get_settings.cache_clear()

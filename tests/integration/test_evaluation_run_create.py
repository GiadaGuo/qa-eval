from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.calculators.protocol import CalculatorLoadError
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _fk_enforcing_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _seed_contract(session: Session):
    dataset_id, version_id = uuid4(), uuid4()
    session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{'0' * 64}",
            artifact_refs={
                "manifest": {"uri": "datasets/manifest.json"},
                "normalized": {"uri": "datasets/normalized.jsonl", "sha256": "0" * 64},
                "field_mapping": {
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {"kind": "CONTENT", "role": "article.body.plain"},
                        }
                    ]
                },
            },
        )
    )
    session.flush()
    MetricRegistryService(session).sync()
    contract = QualityContractService(session).publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "duplicate gate",
                "version_label": "v1",
                "dataset_version_id": version_id,
                "metric_key": "02080103",
                "metric_version": "1.0.0",
                "measurement": "exact_affected_record_ratio",
                "calculator_key": "text_duplicate_degree",
                "calculator_version": "2.0.0",
                "parameters": {"content_role": "article.body.plain"},
                "rule": {
                    "operator": "LTE",
                    "threshold": 0.5,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        )
    )
    session.commit()
    return contract


def test_create_run_persists_plan_and_run_with_fk_enforced(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session)
        service = EvaluationRunService(session, store)

        run = service.create(contract.contract.contract_version_id)
        session.commit()

        plan_row = session.get(PhysicalEvaluationPlanModel, run.physical_plan_id)
        run_row = session.get(EvaluationRunModel, run.run_id)

    assert plan_row is not None
    assert run_row is not None
    assert run_row.physical_plan_id == plan_row.physical_plan_id
    assert run_row.status == "PENDING"


def test_real_database_startup_sweep_converges_owned_run_and_stage(
    db_session, tmp_path
):
    """PostgreSQL integration: worker loss is committed as a terminal fact."""

    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    contract = _seed_contract(db_session)
    service = EvaluationRunService(db_session, store)
    run = service.create(contract.contract.contract_version_id)
    run = service.claim_pending_run(run.run_id, worker_id="dead-worker")
    row = db_session.get(EvaluationRunModel, run.run_id)
    started = datetime.now(UTC)
    running = run.model_copy(update={"status": "RUNNING", "started_at": started})
    row.status = "RUNNING"
    row.started_at = started
    row.document = running.model_dump(mode="json")
    plan = db_session.get(PhysicalEvaluationPlanModel, run.physical_plan_id).document
    stage_plan = plan["stages"][0]
    stage_run_id = uuid4()
    stage_document = {
        "schema_version": "1.0",
        "stage_run_id": str(stage_run_id),
        "run_id": str(run.run_id),
        "physical_plan_id": str(run.physical_plan_id),
        "stage_id": stage_plan["stage_id"],
        "attempt": 1,
        "status": "RUNNING",
        "calculator": stage_plan["calculator"],
        "resolved_parameters": stage_plan["resolved_parameters"],
        "parameters_fingerprint": f"sha256:{'1' * 64}",
        "input_fingerprint": run.input_fingerprint,
        "output_artifacts": [],
        "stats": {},
        "started_at": started.isoformat(),
        "finished_at": None,
        "error": None,
    }
    db_session.add(
        StageRunModel(
            stage_run_id=stage_run_id,
            run_id=run.run_id,
            physical_plan_id=run.physical_plan_id,
            stage_id=stage_plan["stage_id"],
            attempt=1,
            status="RUNNING",
            document=stage_document,
        )
    )
    db_session.commit()

    assert service.sweep_interrupted(current_worker_id="replacement-worker") == 1
    db_session.expire_all()
    recovered_run = db_session.get(EvaluationRunModel, run.run_id)
    recovered_stage = db_session.get(StageRunModel, stage_run_id)

    assert recovered_run.status == "FAILED"
    assert recovered_run.finished_at is not None
    assert recovered_run.document["error"]["error_category"] == "WORKER_INTERRUPTED"
    assert recovered_stage.status == "FAILED"
    assert recovered_stage.document["finished_at"] is not None
    assert recovered_stage.document["error"]["code"] == "WORKER_INTERRUPTED"

    # Idempotent and terminal-protected: repeated startup cannot rewrite it.
    finished_at = recovered_run.finished_at
    assert service.sweep_interrupted(current_worker_id="third-worker") == 0
    db_session.refresh(recovered_run)
    assert recovered_run.status == "FAILED"
    assert recovered_run.finished_at == finished_at


def test_real_database_failure_transition_survives_business_rollback(
    db_session, tmp_path
):
    """PostgreSQL integration: FAILED is written after a poisoned transaction."""

    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    contract = _seed_contract(db_session)
    service = EvaluationRunService(db_session, store)
    run = service.create(contract.contract.contract_version_id)
    run = service.claim_pending_run(run.run_id, worker_id="rollback-worker")
    row = db_session.get(EvaluationRunModel, run.run_id)
    started = datetime.now(UTC)
    running = run.model_copy(update={"status": "RUNNING", "started_at": started})
    row.status = "RUNNING"
    row.started_at = started
    row.document = running.model_dump(mode="json")
    db_session.commit()

    # Simulate an execution transaction that must be discarded before failure
    # persistence can start.
    row.document = {**row.document, "error": {"uncommitted": True}}
    db_session.flush()
    failed = service.fail_running_run(run.run_id, RuntimeError("worker crashed"))

    db_session.expire_all()
    persisted = db_session.get(EvaluationRunModel, run.run_id)
    assert failed is not None
    assert persisted.status == "FAILED"
    assert persisted.finished_at is not None
    assert persisted.document["error"]["phase"] == "BACKGROUND_EXECUTION"
    assert "uncommitted" not in persisted.document["error"]

    # A late callback cannot replace the committed terminal fact.
    terminal_finished_at = persisted.finished_at
    repeated = service.fail_running_run(run.run_id, RuntimeError("late callback"))
    assert repeated.status == "FAILED"
    db_session.refresh(persisted)
    assert persisted.status == "FAILED"
    assert persisted.finished_at == terminal_finished_at


def test_run_row_rejected_when_physical_plan_missing():
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        contract = _seed_contract(session)
        session.add(
            EvaluationRunModel(
                run_id=uuid4(),
                physical_plan_id=uuid4(),
                dataset_version_id=contract.target_dataset_version_id,
                contract_version_id=contract.contract.contract_version_id,
                status="PENDING",
                document={},
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert session.scalars(select(EvaluationRunModel)).all() == []


def test_create_run_rolls_back_plan_when_transaction_aborts(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session)
        service = EvaluationRunService(session, store)

        run = service.create(contract.contract.contract_version_id)
        session.rollback()

        assert session.get(PhysicalEvaluationPlanModel, run.physical_plan_id) is None
        assert session.get(EvaluationRunModel, run.run_id) is None


def test_create_run_rejects_unknown_contract_version(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        service = EvaluationRunService(session, store)
        with pytest.raises(ValueError, match="not found"):
            service.create(uuid4())


def test_create_run_rejects_stale_registry_before_persisting_plan(tmp_path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract = _seed_contract(session)
        descriptor = session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key="text_duplicate_degree",
                version="2.0.0",
            )
        )
        descriptor.document = {
            **descriptor.document,
            "entrypoint": "qa_eval.calculators.text_duplicate_degree:calculate",
            "code_digest": f"sha256:{'0' * 64}",
        }
        session.commit()

        with pytest.raises(CalculatorLoadError, match="platform Protocol") as error:
            EvaluationRunService(session, store).create(
                contract.contract.contract_version_id
            )

        assert error.value.code == "CALCULATOR_LOAD_ERROR"
        assert session.scalars(select(PhysicalEvaluationPlanModel)).all() == []
        assert session.scalars(select(EvaluationRunModel)).all() == []

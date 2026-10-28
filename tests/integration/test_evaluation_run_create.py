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

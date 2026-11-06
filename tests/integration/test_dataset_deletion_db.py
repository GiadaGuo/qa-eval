# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 数据集 / 版本删除的数据库集成测试：
# - 依赖检查（RESTRICT 外键对应的 5 张下游表）
# - 同事务清理 manifest / profile
# - 阻止删除时不产生部分修改
# ====================================================

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from qa_eval.application.services.dataset_deletion_service import (
    DatasetDeletionService,
    process_artifact_cleanup_tasks,
)
from qa_eval.application.services.dataset_service import DatasetService
from qa_eval.application.services.dataset_version_service import (
    DatasetVersionService,
)
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.infrastructure.db.models.artifact_cleanup import (
    ArtifactCleanupTaskModel,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_manifest import (
    DatasetManifestModel,
)
from qa_eval.infrastructure.db.models.dataset_profile import (
    DatasetProfileModel,
)
from qa_eval.infrastructure.db.models.dataset_version import (
    DatasetVersionModel,
)
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    PhysicalEvaluationPlanModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    QualityReportModel,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_profile import (
    SQLAlchemyDatasetProfileRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_document_version import (
    SQLAlchemyDocumentVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _dataset() -> DatasetModel:
    return DatasetModel(id=uuid4(), name=f"ds-{uuid4()}", modality=["text"])


def _version(dataset_id) -> DatasetVersionModel:
    return DatasetVersionModel(
        id=uuid4(),
        dataset_id=dataset_id,
        version=f"v-{uuid4().hex[:8]}",
        status="PUBLISHED",
    )


def _manifest(version_id) -> DatasetManifestModel:
    return DatasetManifestModel(
        id=uuid4(),
        dataset_version_id=version_id,
        document={"schema_version": "2.0"},
        checksum_sha256="0" * 64,
    )


def _profile(version_id) -> DatasetProfileModel:
    return DatasetProfileModel(
        id=uuid4(),
        dataset_version_id=version_id,
        document={"schema_version": "1.0"},
        checksum_sha256="0" * 64,
        profiler_key="base",
        profiler_version="1.0.0",
        config_fingerprint="sha256:0",
    )


def _contract(version_id) -> QualityContractVersionModel:
    return QualityContractVersionModel(
        contract_version_id=uuid4(),
        contract_id=uuid4(),
        target_dataset_version_id=version_id,
        version_label="c1",
        name="contract",
        status="PUBLISHED",
        fingerprint=f"sha256:{uuid4().hex}",
        document={},
        published_at=datetime.now(UTC),
    )


def _physical_plan(version_id, contract_version_id) -> PhysicalEvaluationPlanModel:
    return PhysicalEvaluationPlanModel(
        physical_plan_id=uuid4(),
        dataset_version_id=version_id,
        contract_version_id=contract_version_id,
        semantic_fingerprint=f"sha256:{uuid4().hex}",
        document={},
    )


def _evaluation_run(version_id, contract_version_id, plan) -> EvaluationRunModel:
    return EvaluationRunModel(
        run_id=uuid4(),
        physical_plan_id=plan.physical_plan_id,
        dataset_version_id=version_id,
        contract_version_id=contract_version_id,
        status="SUCCEEDED",
        document={},
    )


def _services(db_session, tmp_path):
    dataset_repo = SQLAlchemyDatasetRepository(db_session)
    version_repo = SQLAlchemyDatasetVersionRepository(db_session)
    document_repo = SQLAlchemyDocumentVersionRepository(db_session)
    profile_repo = SQLAlchemyDatasetProfileRepository(db_session)
    artifact_store = LocalFileSystemArtifactStore(Path(tmp_path))
    return (
        DatasetService(dataset_repo, version_repo, document_repo),
        DatasetVersionService(
            dataset_repository=dataset_repo,
            version_repository=version_repo,
            profile_repository=profile_repo,
            artifact_store=artifact_store,
        ),
    )


def test_delete_version_cleans_manifest_and_profile(db_session, tmp_path):
    _, version_service = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    db_session.add(_manifest(version.id))
    db_session.add(_profile(version.id))
    db_session.flush()

    assert version_service.delete_version(dataset.id, version.id) is True

    assert db_session.get(DatasetVersionModel, version.id) is None
    assert (
        db_session.query(DatasetManifestModel)
        .filter_by(dataset_version_id=version.id)
        .count()
        == 0
    )
    assert (
        db_session.query(DatasetProfileModel)
        .filter_by(dataset_version_id=version.id)
        .count()
        == 0
    )
    # 数据集本身保留
    assert db_session.get(DatasetModel, dataset.id) is not None


def test_delete_missing_version_returns_false(db_session, tmp_path):
    _, version_service = _services(db_session, tmp_path)

    assert version_service.delete_version(uuid4(), uuid4()) is False


def test_delete_version_of_other_dataset_raises(db_session, tmp_path):
    _, version_service = _services(db_session, tmp_path)

    dataset_a = _dataset()
    dataset_b = _dataset()
    db_session.add_all([dataset_a, dataset_b])
    db_session.flush()
    version = _version(dataset_a.id)
    db_session.add(version)
    db_session.flush()

    with pytest.raises(ValueError, match="does not belong to dataset"):
        version_service.delete_version(dataset_b.id, version.id)

    assert db_session.get(DatasetVersionModel, version.id) is not None


def test_delete_version_blocked_by_evaluation_run(db_session, tmp_path):
    _, version_service = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    db_session.add(_manifest(version.id))
    db_session.flush()

    contract = _contract(version.id)
    db_session.add(contract)
    db_session.flush()
    plan = _physical_plan(version.id, contract.contract_version_id)
    db_session.add(plan)
    db_session.flush()
    run = _evaluation_run(version.id, contract.contract_version_id, plan)
    db_session.add(run)
    db_session.add(
        QualityReportModel(
            report_id=uuid4(),
            run_id=run.run_id,
            dataset_version_id=version.id,
            contract_version_id=contract.contract_version_id,
            execution_status="COMPLETED",
            gate_status="PASSED",
            document={},
        )
    )
    db_session.flush()

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        version_service.delete_version(dataset.id, version.id)

    assert exc_info.value.dependencies["evaluation_runs"] == 1
    assert exc_info.value.dependencies["quality_reports"] == 1
    assert exc_info.value.dependencies["quality_contract_versions"] == 1
    assert exc_info.value.dependencies["physical_evaluation_plans"] == 1

    # 阻止删除时不得留下部分删除的数据
    assert db_session.get(DatasetVersionModel, version.id) is not None
    assert db_session.get(DatasetModel, dataset.id) is not None
    assert (
        db_session.query(DatasetManifestModel)
        .filter_by(dataset_version_id=version.id)
        .count()
        == 1
    )
    assert db_session.get(EvaluationRunModel, run.run_id) is not None


def test_delete_version_blocked_by_quality_contract_only(db_session, tmp_path):
    _, version_service = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    db_session.add(_contract(version.id))
    db_session.flush()

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        version_service.delete_version(dataset.id, version.id)

    assert exc_info.value.dependencies["quality_contract_versions"] == 1
    assert db_session.get(DatasetVersionModel, version.id) is not None


def test_delete_dataset_removes_all_clean_versions(db_session, tmp_path):
    dataset_service, _ = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    for _ in range(3):
        version = _version(dataset.id)
        db_session.add(version)
        db_session.flush()
        db_session.add(_manifest(version.id))
        db_session.add(_profile(version.id))
    db_session.flush()
    dataset_id = dataset.id

    assert dataset_service.delete_dataset(dataset_id) is True

    assert db_session.get(DatasetModel, dataset_id) is None
    assert (
        db_session.query(DatasetVersionModel)
        .filter_by(dataset_id=dataset_id)
        .count()
        == 0
    )
    assert (
        db_session.query(DatasetManifestModel)
        .join(
            DatasetVersionModel,
            DatasetManifestModel.dataset_version_id
            == DatasetVersionModel.id,
        )
        .filter(DatasetVersionModel.dataset_id == dataset_id)
        .count()
        == 0
    )


def test_delete_dataset_without_versions(db_session, tmp_path):
    dataset_service, _ = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()

    assert dataset_service.delete_dataset(dataset.id) is True
    assert db_session.get(DatasetModel, dataset.id) is None


def test_delete_dataset_blocked_by_protected_version(db_session, tmp_path):
    dataset_service, _ = _services(db_session, tmp_path)

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    clean = _version(dataset.id)
    protected = _version(dataset.id)
    db_session.add_all([clean, protected])
    db_session.flush()
    db_session.add(_manifest(clean.id))
    db_session.flush()

    contract = _contract(protected.id)
    db_session.add(contract)
    db_session.flush()
    plan = _physical_plan(protected.id, contract.contract_version_id)
    db_session.add(plan)
    db_session.flush()
    run = _evaluation_run(protected.id, contract.contract_version_id, plan)
    db_session.add(run)
    db_session.flush()

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        dataset_service.delete_dataset(dataset.id)

    assert exc_info.value.code == "DATASET_IN_USE"
    assert exc_info.value.dependencies["evaluation_runs"] == 1
    assert exc_info.value.dependencies["protected_versions"] == 1

    # 即使部分版本可删，也不得删除任何版本（无部分删除）
    assert db_session.get(DatasetVersionModel, clean.id) is not None
    assert db_session.get(DatasetVersionModel, protected.id) is not None
    assert db_session.get(DatasetModel, dataset.id) is not None
    assert (
        db_session.query(DatasetManifestModel)
        .filter_by(dataset_version_id=clean.id)
        .count()
        == 1
    )
    assert db_session.get(EvaluationRunModel, run.run_id) is not None


def test_cascade_delete_dataset_removes_completed_evaluation_graph(db_session):
    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    db_session.add_all([_manifest(version.id), _profile(version.id)])
    contract = _contract(version.id)
    db_session.add(contract)
    db_session.flush()
    plan = _physical_plan(version.id, contract.contract_version_id)
    db_session.add(plan)
    db_session.flush()
    run = _evaluation_run(version.id, contract.contract_version_id, plan)
    db_session.add(run)
    db_session.add(
        QualityReportModel(
            report_id=uuid4(), run_id=run.run_id,
            dataset_version_id=version.id,
            contract_version_id=contract.contract_version_id,
            execution_status="COMPLETED", gate_status="PASSED", document={},
        )
    )
    db_session.flush()

    service = DatasetDeletionService(db_session)
    preview = service.preview(dataset.id)
    assert preview is not None
    assert preview["resources"]["evaluation_runs"] == 1
    assert preview["resources"]["quality_reports"] == 1
    assert preview["resources"]["protected_versions"] == 1
    assert preview["can_cascade_delete"] is True

    assert service.cascade_delete(dataset.id) is True
    assert db_session.get(DatasetModel, dataset.id) is None
    assert db_session.get(DatasetVersionModel, version.id) is None
    assert db_session.get(EvaluationRunModel, run.run_id) is None
    assert db_session.get(QualityContractVersionModel, contract.contract_version_id) is None
    assert db_session.query(ArtifactCleanupTaskModel).count() == 2


def test_cascade_delete_blocks_active_run_without_partial_changes(db_session):
    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    contract = _contract(version.id)
    db_session.add(contract)
    db_session.flush()
    plan = _physical_plan(version.id, contract.contract_version_id)
    db_session.add(plan)
    db_session.flush()
    run = _evaluation_run(version.id, contract.contract_version_id, plan)
    run.status = "RUNNING"
    db_session.add(run)
    db_session.flush()

    service = DatasetDeletionService(db_session)
    preview = service.preview(dataset.id)
    assert preview is not None
    assert preview["can_cascade_delete"] is False
    assert preview["blockers"][0]["resource"] == "active_evaluation_runs"

    with pytest.raises(DatasetVersionInUseError) as exc_info:
        service.cascade_delete(dataset.id)

    assert exc_info.value.code == "DATASET_DELETE_BLOCKED"
    assert db_session.get(DatasetModel, dataset.id) is not None
    assert db_session.get(DatasetVersionModel, version.id) is not None
    assert db_session.get(EvaluationRunModel, run.run_id) is not None
    assert db_session.query(ArtifactCleanupTaskModel).count() == 0


def test_cascade_delete_rolls_back_all_rows_on_database_failure(
    db_session, monkeypatch
):
    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    db_session.add(_manifest(version.id))
    db_session.flush()

    service = DatasetDeletionService(db_session)
    original_delete = service._delete_in
    calls = 0

    def fail_during_delete(model, column, ids):
        nonlocal calls
        calls += 1
        original_delete(model, column, ids)
        if calls == 3:
            raise RuntimeError("simulated database failure")

    monkeypatch.setattr(service, "_delete_in", fail_during_delete)
    with (
        pytest.raises(RuntimeError, match="simulated database failure"),
        db_session.begin_nested(),
    ):
        service.cascade_delete(dataset.id)

    assert db_session.get(DatasetModel, dataset.id) is not None
    assert db_session.get(DatasetVersionModel, version.id) is not None
    assert (
        db_session.query(DatasetManifestModel)
        .filter_by(dataset_version_id=version.id)
        .count()
        == 1
    )
    assert db_session.query(ArtifactCleanupTaskModel).count() == 0


def test_artifact_cleanup_outbox_is_idempotent_and_retryable(db_session):
    class Store:
        def __init__(self):
            self.prefixes = []

        def delete_prefix(self, value):
            self.prefixes.append(value)

        def delete(self, value):
            raise AssertionError(f"unexpected object cleanup: {value}")

    task = ArtifactCleanupTaskModel(
        target="datasets/example/", target_type="PREFIX", status="PENDING", attempts=0
    )
    db_session.add(task)
    db_session.flush()
    store = Store()

    assert process_artifact_cleanup_tasks(db_session, store) == 1
    assert process_artifact_cleanup_tasks(db_session, store) == 0
    assert store.prefixes == ["datasets/example/"]
    assert task.status == "COMPLETED"
    assert task.attempts == 1


def test_database_restrict_backstops_concurrent_reference(
    db_session, tmp_path
):
    """绕过应用层检查直接删除时，数据库 RESTRICT 约束兜底。"""

    dataset = _dataset()
    db_session.add(dataset)
    db_session.flush()
    version = _version(dataset.id)
    db_session.add(version)
    db_session.flush()
    contract = _contract(version.id)
    db_session.add(contract)
    db_session.flush()

    repo = SQLAlchemyDatasetVersionRepository(db_session)

    with pytest.raises(IntegrityError):
        repo.delete(version.id)

    db_session.rollback()

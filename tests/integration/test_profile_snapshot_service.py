from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.snapshots import ProfileSnapshotService
from qa_eval.infrastructure.db.models.artifact_cleanup import ArtifactCleanupTaskModel
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_card import DatasetCardModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.profile_snapshot import ProfileSnapshotModel
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _setup(db_session, tmp_path: Path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id, profile_id = uuid4(), uuid4(), uuid4()
    manifest_path = store.prepare_write("datasets/manifest.json")
    manifest_path.write_text('{"schema_version":"2.0"}', encoding="utf-8")
    manifest = store.commit("datasets/manifest.json")
    profile_document = {
        "schema_version": "1.0",
        "profile_id": str(profile_id),
        "dataset_version_id": str(version_id),
        "structure": {"content_roles": ["article.body.plain"]},
    }
    db_session.add(DatasetModel(id=dataset_id, name="snapshot", modality=["text"]))
    db_session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint="sha256:" + "a" * 64,
            artifact_refs={
                "manifest": {"uri": manifest.uri, "sha256": manifest.sha256},
                "normalized": {"uri": "normalized.jsonl", "sha256": "b" * 64},
                "field_mapping": {
                    "schema_version": "2.0",
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {
                                "kind": "CONTENT",
                                "role": "article.body.plain",
                                "modality": "text",
                            },
                        }
                    ],
                },
            },
            published_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    db_session.add(
        DatasetProfileModel(
            id=profile_id,
            dataset_version_id=version_id,
            document=profile_document,
            checksum_sha256=_hash(profile_document),
            profiler_key="base_dataset_profiler",
            profiler_version="1.0.0",
            config_fingerprint="sha256:" + "c" * 64,
        )
    )
    card_document = {
        "schema_version": "1.0",
        "basic_information": {"name": "first"},
        "content_characteristics": {},
        "construction_process": {},
        "application_description": {},
    }
    card = DatasetCardModel(
        id=uuid4(),
        dataset_version_id=version_id,
        revision=1,
        lifecycle_status="PUBLISHED",
        document=card_document,
        content_sha256=_hash(card_document),
        published_at=datetime.now(UTC),
    )
    db_session.add(card)
    db_session.flush()
    return store, version_id, card


def test_snapshot_is_content_addressed_and_card_change_creates_new_snapshot(
    db_session, tmp_path: Path
) -> None:
    store, version_id, first_card = _setup(db_session, tmp_path)
    service = ProfileSnapshotService(db_session, store)
    bindings = [
        {
            "metric": {"key": "temporary", "version": "1.0.0"},
            "calculator": {"key": "calculator", "version": "1.0.0"},
            "parameter_fingerprint": "sha256:" + "d" * 64,
        }
    ]

    first, first_ref = service.create_or_reuse(
        version_id,
        rule_set_refs=["bg01-card-rules@1.0.0"],
        calculator_bindings=bindings,
    )
    reused, reused_ref = service.create_or_reuse(
        version_id,
        rule_set_refs=["bg01-card-rules@1.0.0"],
        calculator_bindings=bindings,
    )

    assert reused.snapshot_id == first.snapshot_id
    assert reused_ref == first_ref
    assert db_session.query(ProfileSnapshotModel).count() == 1
    assert store.exists(first_ref.artifact.uri)

    first_card.lifecycle_status = "SUPERSEDED"
    second_document = {**first_card.document, "basic_information": {"name": "second"}}
    db_session.add(
        DatasetCardModel(
            id=uuid4(),
            dataset_version_id=version_id,
            revision=2,
            lifecycle_status="PUBLISHED",
            document=second_document,
            content_sha256=_hash(second_document),
            published_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    second, _ = service.create_or_reuse(
        version_id,
        rule_set_refs=["bg01-card-rules@1.0.0"],
        calculator_bindings=bindings,
    )

    assert second.snapshot_id != first.snapshot_id
    assert second.content_sha256 != first.content_sha256
    assert service.get(first.snapshot_id) == first
    assert db_session.query(ProfileSnapshotModel).count() == 2


def test_version_delete_queues_dataset_and_snapshot_artifact_cleanup(
    db_session, tmp_path: Path
) -> None:
    store, version_id, _ = _setup(db_session, tmp_path)
    ProfileSnapshotService(db_session, store).create_or_reuse(version_id)

    assert SQLAlchemyDatasetVersionRepository(db_session).delete(version_id) is True
    targets = set(db_session.scalars(select(ArtifactCleanupTaskModel.target)).all())
    assert any(value.endswith(f"/{version_id}/") for value in targets)
    assert f"profile-snapshots/{version_id}/" in targets
    assert db_session.query(ProfileSnapshotModel).count() == 0


def test_run_binds_snapshot_and_uses_its_hash_as_input_fingerprint(
    db_session, tmp_path: Path
) -> None:
    store, version_id, _ = _setup(db_session, tmp_path)
    MetricRegistryService(db_session).sync()
    descriptor = db_session.scalar(
        select(CalculatorImplementationModel).where(
            CalculatorImplementationModel.calculator_key == "text_duplicate_degree",
            CalculatorImplementationModel.version == "2.0.0",
        )
    )
    descriptor.document = {
        **descriptor.document,
        "input_artifact_requirements": ["PROFILE_SNAPSHOT"],
    }
    db_session.flush()
    contract = QualityContractService(db_session).publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "snapshot binding",
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

    run = EvaluationRunService(db_session, store).create(
        contract.contract.contract_version_id
    )
    snapshot_ref = run.execution["input_artifacts"]["PROFILE_SNAPSHOT"]

    assert run.input_fingerprint == f"sha256:{snapshot_ref['content_sha256']}"
    assert store.exists(snapshot_ref["artifact"]["uri"])
    assert db_session.query(ProfileSnapshotModel).count() == 1

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.contracts.execution import EvaluationRunV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import StageRunModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


class MaterializeOnlyStore(LocalFileSystemArtifactStore):
    def read(self, relative_path: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError(f"read() must not materialize Calculator input: {relative_path}")


def test_worker_materializes_declared_inputs_without_full_read(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = MaterializeOnlyStore(tmp_path / "artifacts")
    normalized_path = store.prepare_write("normalized.jsonl")
    normalized_path.write_text('{"sample_id":"1"}\n', encoding="utf-8")
    normalized = store.commit("normalized.jsonl")
    manifest_path = store.prepare_write("manifest.json")
    manifest_path.write_text('{"schema_version":"2.0"}', encoding="utf-8")
    manifest = store.commit("manifest.json")
    snapshot_path = store.prepare_write("snapshot.json")
    snapshot_path.write_text('{"schema_version":"1.0"}', encoding="utf-8")
    snapshot = store.commit("snapshot.json")

    dataset_id, version_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="multi", modality=["text"]))
        version = DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            artifact_refs={
                "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                "manifest": {"uri": manifest.uri, "sha256": manifest.sha256},
            },
        )
        session.add(version)
        session.flush()
        run = EvaluationRunV1.model_validate(
            {
                "run_id": uuid4(),
                "physical_plan_id": uuid4(),
                "dataset_version_id": version_id,
                "contract_version_id": uuid4(),
                "status": "PENDING",
                "input_fingerprint": "sha256:" + "a" * 64,
                "created_at": "2026-11-17T00:00:00+08:00",
                "execution": {
                    "input_artifacts": {
                        "PROFILE_SNAPSHOT": {
                            "artifact": {
                                "uri": snapshot.uri,
                                "sha256": snapshot.sha256,
                            }
                        }
                    }
                },
            }
        )
        root = tmp_path / "materialized"
        artifacts = EvaluationRunService(session, store)._materialize_inputs(
            run,
            version,
            ["NORMALIZED_DATA", "DATASET_MANIFEST", "PROFILE_SNAPSHOT"],
            root,
        )

    assert set(artifacts) == {
        "NORMALIZED_DATA",
        "DATASET_MANIFEST",
        "PROFILE_SNAPSHOT",
    }
    assert json.loads(artifacts["DATASET_MANIFEST"].read_text())["schema_version"] == "2.0"


def test_progress_callback_persists_bounded_stage_stats(tmp_path: Path) -> None:
    stage_run_id = uuid4()
    row = Mock()
    row.document = {
        "schema_version": "1.0",
        "stage_run_id": str(stage_run_id),
        "run_id": str(uuid4()),
        "physical_plan_id": str(uuid4()),
        "stage_id": "stage",
        "attempt": 1,
        "status": "RUNNING",
        "calculator": {"key": "test"},
        "resolved_parameters": {},
        "parameters_fingerprint": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "b" * 64,
        "output_artifacts": [],
        "stats": {},
    }
    session = Mock()
    session.get.side_effect = lambda model, _identity: (
        row if model is StageRunModel else None
    )
    service = EvaluationRunService(
        session, LocalFileSystemArtifactStore(tmp_path / "artifacts")
    )

    service._progress_callback(stage_run_id)(25, {"eligible": 20})

    assert row.document["stats"]["processed"] == 25
    assert row.document["stats"]["eligible"] == 20
    assert "updated_at" in row.document["stats"]
    session.commit.assert_called_once()

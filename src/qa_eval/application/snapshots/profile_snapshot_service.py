from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qa_eval.contracts.snapshots import ProfileSnapshotRefV1, ProfileSnapshotV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.dataset_card import DatasetCardModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.profile_snapshot import ProfileSnapshotModel
from qa_eval.paths import PROJECT_ROOT


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ProfileSnapshotService:
    def __init__(self, session: Session, artifact_store: ArtifactStore):
        self.session = session
        self.artifact_store = artifact_store

    def create_or_reuse(
        self,
        dataset_version_id: UUID,
        *,
        rule_set_refs: list[str] | None = None,
        calculator_bindings: list[dict] | None = None,
    ) -> tuple[ProfileSnapshotV1, ProfileSnapshotRefV1]:
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None or version.status != "PUBLISHED":
            raise ValueError("ProfileSnapshot requires a PUBLISHED DatasetVersion")
        refs = version.artifact_refs or {}
        manifest_ref = refs.get("manifest")
        if not isinstance(manifest_ref, dict) or not manifest_ref.get("uri"):
            raise ValueError("ProfileSnapshot requires a DatasetManifest artifact")
        observed = self.session.scalar(
            select(DatasetProfileModel)
            .where(DatasetProfileModel.dataset_version_id == dataset_version_id)
            .order_by(DatasetProfileModel.created_at.desc())
            .limit(1)
        )
        if observed is None:
            raise ValueError("ProfileSnapshot requires an observed DatasetProfile")
        card = self.session.scalar(
            select(DatasetCardModel)
            .where(
                DatasetCardModel.dataset_version_id == dataset_version_id,
                DatasetCardModel.lifecycle_status == "PUBLISHED",
            )
            .order_by(DatasetCardModel.revision.desc())
            .limit(1)
        )
        manifest_sha = self._materialized_checksum(manifest_ref)
        rules = [self._rule_identity(value) for value in sorted(rule_set_refs or [])]
        mapping = refs.get("field_mapping")
        if mapping is not None:
            rules.append(
                {
                    "key": "field_mapping",
                    "version": str(mapping.get("schema_version", "unknown"))
                    if isinstance(mapping, dict)
                    else "unknown",
                    "sha256": _sha256(_canonical_bytes(mapping)),
                }
            )
        semantic = {
            "dataset_version": {
                "id": str(version.id),
                "content_fingerprint": version.content_fingerprint,
            },
            "card": (
                {
                    "card_id": str(card.id),
                    "revision": card.revision,
                    "lifecycle_status": card.lifecycle_status,
                    "content_sha256": card.content_sha256,
                    "document": card.document,
                }
                if card is not None
                else None
            ),
            "observed_profile": {
                "profile_id": str(observed.id),
                "profiler": {
                    "key": observed.profiler_key,
                    "version": observed.profiler_version,
                    "config_fingerprint": observed.config_fingerprint,
                },
                "checksum_sha256": observed.checksum_sha256,
            },
            "manifest": {"uri": str(manifest_ref["uri"]), "sha256": manifest_sha},
            "rules": rules,
            "calculator_bindings": calculator_bindings or [],
        }
        content_sha = _sha256(_canonical_bytes(semantic))
        existing = self.session.scalar(
            select(ProfileSnapshotModel).where(
                ProfileSnapshotModel.dataset_version_id == dataset_version_id,
                ProfileSnapshotModel.content_sha256 == content_sha,
            )
        )
        if existing is not None:
            return self._validated_existing(existing)

        snapshot_id = uuid5(
            NAMESPACE_URL, f"qa-eval:profile-snapshot:{dataset_version_id}:{content_sha}"
        )
        created_at = datetime.now(UTC)
        document = ProfileSnapshotV1.model_validate(
            {
                "snapshot_id": snapshot_id,
                "dataset_version_id": dataset_version_id,
                **semantic,
                "content_sha256": content_sha,
                "input_fingerprint": f"sha256:{content_sha}",
                "created_at": created_at,
            }
        )
        relative = f"profile-snapshots/{dataset_version_id}/{content_sha}.json"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "profile_snapshot.json"
            source.write_bytes(_canonical_bytes(document.model_dump(mode="json")))
            stored = self.artifact_store.put_file(relative, source)
        model = ProfileSnapshotModel(
            id=snapshot_id,
            dataset_version_id=dataset_version_id,
            card_revision_id=card.id if card is not None else None,
            observed_profile_id=observed.id,
            content_sha256=content_sha,
            artifact_uri=stored.uri,
            artifact_sha256=stored.sha256,
            rule_versions=rules,
            document=document.model_dump(mode="json"),
        )
        try:
            with self.session.begin_nested():
                self.session.add(model)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(ProfileSnapshotModel).where(
                    ProfileSnapshotModel.dataset_version_id == dataset_version_id,
                    ProfileSnapshotModel.content_sha256 == content_sha,
                )
            )
            if existing is None:
                self.artifact_store.delete(stored.uri)
                raise
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "profile_snapshot.json"
                source.write_bytes(_canonical_bytes(existing.document))
                restored = self.artifact_store.put_file(existing.artifact_uri, source)
            if restored.sha256 != existing.artifact_sha256:
                raise ValueError("Concurrent ProfileSnapshot artifact is inconsistent")
            return self._validated_existing(existing)
        except Exception:
            self.artifact_store.delete(stored.uri)
            raise
        return document, ProfileSnapshotRefV1(
            snapshot_id=snapshot_id,
            content_sha256=content_sha,
            artifact={"uri": stored.uri, "sha256": stored.sha256},
        )

    def get(self, snapshot_id: UUID) -> ProfileSnapshotV1 | None:
        model = self.session.get(ProfileSnapshotModel, snapshot_id)
        return None if model is None else self._validated_existing(model)[0]

    def _validated_existing(
        self, model: ProfileSnapshotModel
    ) -> tuple[ProfileSnapshotV1, ProfileSnapshotRefV1]:
        if not self.artifact_store.exists(model.artifact_uri):
            raise FileNotFoundError(f"ProfileSnapshot artifact missing: {model.artifact_uri}")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            self.artifact_store.materialize(model.artifact_uri, path)
            actual = _sha256_file(path)
        if actual != model.artifact_sha256:
            raise ValueError("ProfileSnapshot artifact checksum mismatch")
        return ProfileSnapshotV1.model_validate(model.document), ProfileSnapshotRefV1(
            snapshot_id=model.id,
            content_sha256=model.content_sha256,
            artifact={"uri": model.artifact_uri, "sha256": model.artifact_sha256},
        )

    def _materialized_checksum(self, artifact_ref: dict) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            self.artifact_store.materialize(str(artifact_ref["uri"]), path)
            actual = _sha256_file(path)
        declared = artifact_ref.get("sha256") or (
            artifact_ref.get("checksum", {}).get("value")
            if isinstance(artifact_ref.get("checksum"), dict)
            else None
        )
        if declared is not None and str(declared).removeprefix("sha256:") != actual:
            raise ValueError("DatasetManifest artifact checksum mismatch")
        return actual

    @staticmethod
    def _rule_identity(reference: str) -> dict:
        if "@" not in reference:
            raise ValueError(f"RuleSet reference must be key@version: {reference}")
        key, version = reference.split("@", 1)
        path = PROJECT_ROOT / "configs" / "card-rules" / f"{key}-{version}.json"
        if not path.is_file():
            raise ValueError(f"RuleSet not found: {reference}")
        return {"key": key, "version": version, "sha256": _sha256_file(path)}

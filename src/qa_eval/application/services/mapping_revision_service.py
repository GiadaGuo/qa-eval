from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qa_eval.application.normalization.record_reader import RawRecordManifest
from qa_eval.contracts.logical_inputs import MappingRevision
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.mapping_revision import MappingRevisionModel


class MappingRevisionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def confirm(self, revision: MappingRevision) -> str:
        version = self.session.get(DatasetVersionModel, revision.dataset_version_id)
        if version is None or version.status != "PUBLISHED":
            raise ValueError("mapping confirmation requires a published DatasetVersion")
        source = (version.artifact_refs or {}).get("raw_record_manifest")
        if source is None:
            raise ValueError("mapping confirmation requires a v3 record Manifest")
        manifest = RawRecordManifest.model_validate(source)
        if (
            manifest.dataset_version_id != revision.dataset_version_id
            or manifest.schema_fingerprint != revision.schema_fingerprint
        ):
            raise ValueError("mapping schema does not match the published Manifest")
        digest = revision.fingerprint()
        key = (revision.dataset_version_id, digest)
        if self.session.get(MappingRevisionModel, key) is not None:
            return digest
        try:
            with self.session.begin_nested():
                self.session.add(MappingRevisionModel(
                    dataset_version_id=revision.dataset_version_id,
                    revision_digest=digest,
                    document=revision.model_dump(mode="json"),
                ))
                self.session.flush()
        except IntegrityError:
            if self.session.get(MappingRevisionModel, key) is None:
                raise
        return digest

    def get(self, dataset_version_id: UUID, digest: str) -> MappingRevision | None:
        stored = self.session.get(MappingRevisionModel, (dataset_version_id, digest))
        if stored is None:
            return None
        value = MappingRevision.model_validate(stored.document)
        if value.fingerprint() != digest:
            raise ValueError("stored mapping checksum mismatch")
        return value

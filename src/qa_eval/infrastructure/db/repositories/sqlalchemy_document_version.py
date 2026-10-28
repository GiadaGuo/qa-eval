# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# DocumentVersion SQLAlchemy 仓储实现。
# ====================================================

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from qa_eval.domain.entities.document_version import (
    DOCUMENT_FORMAT_MARKDOWN,
    DocumentVersion,
)
from qa_eval.domain.repositories.document_version_repository import (
    DocumentVersionRepository,
)
from qa_eval.infrastructure.db.models.document_version import (
    DocumentReportTaskModel,
    DocumentVersionModel,
)

_VERSION_LABEL_PATTERN = re.compile(r"^v(\d+)$")


class SQLAlchemyDocumentVersionRepository(DocumentVersionRepository):
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _to_domain(model: DocumentVersionModel) -> DocumentVersion:
        document = model.document or {}
        return DocumentVersion(
            id=model.id,
            dataset_id=model.dataset_id,
            version=model.version,
            filename=document.get("filename", ""),
            format=document.get("format", DOCUMENT_FORMAT_MARKDOWN),
            size_bytes=model.size_bytes,
            sha256=model.sha256,
            storage_uri=model.storage_uri,
            parsed_artifact_uri=document.get("parsed_artifact_uri"),
            created_at=model.created_at,
        )

    @staticmethod
    def _to_document(version: DocumentVersion) -> dict:
        return {
            "filename": version.filename,
            "format": version.format,
            "parsed_artifact_uri": version.parsed_artifact_uri,
        }

    def add(self, version: DocumentVersion) -> DocumentVersion:
        model = DocumentVersionModel(
            id=version.id,
            dataset_id=version.dataset_id,
            version=version.version,
            sha256=version.sha256,
            size_bytes=version.size_bytes,
            storage_uri=version.storage_uri,
            document=self._to_document(version),
        )
        self.session.add(model)
        self.session.flush()
        self.session.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, version_id: UUID) -> DocumentVersion | None:
        model = self.session.get(DocumentVersionModel, version_id)
        if model is None:
            return None
        return self._to_domain(model)

    def list_by_dataset(self, dataset_id: UUID) -> list[DocumentVersion]:
        stmt = (
            select(DocumentVersionModel)
            .where(DocumentVersionModel.dataset_id == dataset_id)
            .order_by(DocumentVersionModel.created_at.desc())
        )
        models = self.session.scalars(stmt).all()
        return [self._to_domain(model) for model in models]

    def next_version_label(self, dataset_id: UUID) -> str:
        stmt = select(DocumentVersionModel.version).where(
            DocumentVersionModel.dataset_id == dataset_id
        )
        labels = self.session.scalars(stmt).all()
        numbers = [
            int(match.group(1))
            for match in (_VERSION_LABEL_PATTERN.fullmatch(label) for label in labels)
            if match
        ]
        return f"v{max(numbers) + 1 if numbers else 1}"

    def count_blocking_dependencies(self, version_id: UUID) -> dict[str, int]:
        stmt = (
            select(func.count())
            .select_from(DocumentReportTaskModel)
            .where(DocumentReportTaskModel.document_version_id == version_id)
        )
        return {
            "document_report_tasks": self.session.scalar(stmt) or 0,
        }

    def set_parsed_artifact_uri(self, version_id: UUID, uri: str) -> bool:
        model = self.session.get(DocumentVersionModel, version_id)

        if model is None:
            return False

        document = dict(model.document or {})
        document["parsed_artifact_uri"] = uri
        model.document = document
        self.session.flush()
        return True

    def delete(self, version_id: UUID) -> bool:
        model = self.session.get(DocumentVersionModel, version_id)

        if model is None:
            return False

        self.session.delete(model)
        self.session.flush()

        return True

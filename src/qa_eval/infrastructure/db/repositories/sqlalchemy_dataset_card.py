from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qa_eval.contracts.cards import (
    CandidateCreateRequest,
    DatasetCardDeclarationsV1,
    DatasetCardRevisionV1,
    DatasetProfileFieldCandidateV1,
)
from qa_eval.infrastructure.db.models.dataset_card import (
    DatasetCardModel,
    DatasetProfileFieldCandidateModel,
)
from qa_eval.infrastructure.uuid7 import uuid7


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SQLAlchemyDatasetCardRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _card(model: DatasetCardModel) -> DatasetCardRevisionV1:
        return DatasetCardRevisionV1(
            card_id=model.id,
            dataset_version_id=model.dataset_version_id,
            revision=model.revision,
            lifecycle_status=model.lifecycle_status,
            document=DatasetCardDeclarationsV1.model_validate(model.document),
            content_sha256=model.content_sha256,
            published_at=model.published_at,
            created_at=_aware(model.created_at),
            updated_at=_aware(model.updated_at),
        )

    @staticmethod
    def _candidate(
        model: DatasetProfileFieldCandidateModel,
    ) -> DatasetProfileFieldCandidateV1:
        return DatasetProfileFieldCandidateV1(
            candidate_id=model.id,
            dataset_version_id=model.dataset_version_id,
            card_revision=model.card_revision,
            field_path=model.field_path,
            value=model.value_json,
            value_role=model.value_role,
            source_type=model.source_type,
            source_ref=model.source_ref_json,
            evidence_locator=model.evidence_locator_json,
            extraction_rule_version=model.extraction_rule_version,
            review_status=model.review_status,
            supersedes_id=model.supersedes_id,
            reviewed_by=model.reviewed_by,
            reviewed_at=model.reviewed_at,
            created_at=_aware(model.created_at),
            updated_at=_aware(model.updated_at),
        )

    def next_revision(self, dataset_version_id: UUID) -> int:
        current = self.session.scalar(
            select(func.max(DatasetCardModel.revision)).where(
                DatasetCardModel.dataset_version_id == dataset_version_id
            )
        )
        return int(current or 0) + 1

    def add_card(
        self,
        dataset_version_id: UUID,
        revision: int,
        document: DatasetCardDeclarationsV1,
        *,
        lifecycle_status: str = "DRAFT",
    ) -> DatasetCardRevisionV1:
        serialized = document.model_dump(mode="json")
        model = DatasetCardModel(
            id=uuid7(),
            dataset_version_id=dataset_version_id,
            revision=revision,
            lifecycle_status=lifecycle_status,
            document=serialized,
            content_sha256=canonical_hash(serialized),
        )
        self.session.add(model)
        self.session.flush()
        self.session.refresh(model)
        return self._card(model)

    def get_revision(
        self, dataset_version_id: UUID, revision: int
    ) -> DatasetCardRevisionV1 | None:
        model = self.session.scalar(
            select(DatasetCardModel).where(
                DatasetCardModel.dataset_version_id == dataset_version_id,
                DatasetCardModel.revision == revision,
            )
        )
        return None if model is None else self._card(model)

    def get_latest(
        self, dataset_version_id: UUID, *, lock: bool = False
    ) -> DatasetCardRevisionV1 | None:
        statement = (
            select(DatasetCardModel)
            .where(DatasetCardModel.dataset_version_id == dataset_version_id)
            .order_by(DatasetCardModel.revision.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        return None if model is None else self._card(model)

    def get_latest_published(
        self, dataset_version_id: UUID
    ) -> DatasetCardRevisionV1 | None:
        model = self.session.scalar(
            select(DatasetCardModel)
            .where(
                DatasetCardModel.dataset_version_id == dataset_version_id,
                DatasetCardModel.lifecycle_status == "PUBLISHED",
            )
            .order_by(DatasetCardModel.revision.desc())
            .limit(1)
        )
        return None if model is None else self._card(model)

    def list_cards(self, dataset_version_id: UUID) -> list[DatasetCardRevisionV1]:
        models = self.session.scalars(
            select(DatasetCardModel)
            .where(DatasetCardModel.dataset_version_id == dataset_version_id)
            .order_by(DatasetCardModel.revision.desc())
        ).all()
        return [self._card(model) for model in models]

    def set_status(
        self, dataset_version_id: UUID, revision: int, status: str
    ) -> DatasetCardRevisionV1:
        model = self.session.scalar(
            select(DatasetCardModel).where(
                DatasetCardModel.dataset_version_id == dataset_version_id,
                DatasetCardModel.revision == revision,
            )
        )
        if model is None:
            raise ValueError(f"Dataset card revision not found: {revision}")
        model.lifecycle_status = status
        if status == "PUBLISHED":
            model.published_at = datetime.now(UTC)
        self.session.flush()
        self.session.refresh(model)
        return self._card(model)

    def supersede_other_cards(
        self, dataset_version_id: UUID, except_revision: int
    ) -> None:
        models = self.session.scalars(
            select(DatasetCardModel).where(
                DatasetCardModel.dataset_version_id == dataset_version_id,
                DatasetCardModel.revision != except_revision,
                DatasetCardModel.lifecycle_status.in_(["DRAFT", "CONFIRMED", "PUBLISHED"]),
            )
        ).all()
        for model in models:
            model.lifecycle_status = "SUPERSEDED"
        self.session.flush()

    def add_candidate(
        self, dataset_version_id: UUID, request: CandidateCreateRequest
    ) -> DatasetProfileFieldCandidateV1:
        value_hash = canonical_hash(request.value)
        source_ref = request.source_ref
        source_ref_hash = canonical_hash(source_ref)
        existing = self.session.scalar(
            select(DatasetProfileFieldCandidateModel).where(
                DatasetProfileFieldCandidateModel.dataset_version_id
                == dataset_version_id,
                DatasetProfileFieldCandidateModel.field_path == request.field_path,
                DatasetProfileFieldCandidateModel.value_hash == value_hash,
                DatasetProfileFieldCandidateModel.source_type == request.source_type,
                DatasetProfileFieldCandidateModel.source_ref_hash == source_ref_hash,
            )
        )
        if existing is not None:
            return self._candidate(existing)
        model = DatasetProfileFieldCandidateModel(
            id=uuid7(),
            dataset_version_id=dataset_version_id,
            field_path=request.field_path,
            value_json=request.value,
            value_hash=value_hash,
            value_role=request.value_role,
            source_type=request.source_type,
            source_ref_json=source_ref,
            source_ref_hash=source_ref_hash,
            evidence_locator_json=request.evidence_locator,
            extraction_rule_version=request.extraction_rule_version,
            review_status="PENDING_REVIEW",
        )
        try:
            with self.session.begin_nested():
                self.session.add(model)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(DatasetProfileFieldCandidateModel).where(
                    DatasetProfileFieldCandidateModel.dataset_version_id
                    == dataset_version_id,
                    DatasetProfileFieldCandidateModel.field_path
                    == request.field_path,
                    DatasetProfileFieldCandidateModel.value_hash == value_hash,
                    DatasetProfileFieldCandidateModel.source_type
                    == request.source_type,
                    DatasetProfileFieldCandidateModel.source_ref_hash
                    == source_ref_hash,
                )
            )
            if existing is None:
                raise
            return self._candidate(existing)
        self.session.refresh(model)
        return self._candidate(model)

    def list_candidates(
        self, dataset_version_id: UUID
    ) -> list[DatasetProfileFieldCandidateV1]:
        models = self.session.scalars(
            select(DatasetProfileFieldCandidateModel)
            .where(
                DatasetProfileFieldCandidateModel.dataset_version_id
                == dataset_version_id
            )
            .order_by(DatasetProfileFieldCandidateModel.created_at.desc())
        ).all()
        return [self._candidate(model) for model in models]

    def review_candidate(
        self,
        dataset_version_id: UUID,
        candidate_id: UUID,
        decision: str,
        reviewed_by: str,
    ) -> DatasetProfileFieldCandidateV1:
        model = self.session.get(DatasetProfileFieldCandidateModel, candidate_id)
        if model is None:
            raise ValueError(f"Field candidate not found: {candidate_id}")
        if model.dataset_version_id != dataset_version_id:
            raise ValueError("Field candidate does not belong to DatasetVersion")
        if model.review_status != "PENDING_REVIEW":
            raise ValueError("Field candidate has already been reviewed")
        model.review_status = decision
        model.reviewed_by = reviewed_by
        model.reviewed_at = datetime.now(UTC)
        self.session.flush()
        self.session.refresh(model)
        return self._candidate(model)

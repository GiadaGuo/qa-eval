from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from qa_eval.application.identifiers import uuid7
from qa_eval.contracts.cards import (
    CandidateCreateRequest,
    CandidateReviewRequest,
    DatasetCardDeclarationsV1,
    DatasetCardRevisionV1,
    DatasetCardUpdateRequest,
    DatasetProfileCardRefV1,
    DatasetProfileFieldCandidateV1,
    DatasetProfileObservationsV1_1,
    DatasetProfileProvenanceV1_1,
    DatasetProfileV1_1,
)
from qa_eval.domain.entities.dataset_version import DatasetVersionStatus
from qa_eval.domain.repositories.dataset_profile_repository import (
    DatasetProfileRepository,
)
from qa_eval.domain.repositories.dataset_version_repository import (
    DatasetVersionRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_card import (
    SQLAlchemyDatasetCardRepository,
)


class CardRevisionConflictError(ValueError):
    def __init__(self, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"Card revision conflict: expected {expected_revision}, current {current_revision}"
        )


class DatasetCardService:
    def __init__(
        self,
        version_repository: DatasetVersionRepository,
        profile_repository: DatasetProfileRepository,
        card_repository: SQLAlchemyDatasetCardRepository,
    ):
        self._versions = version_repository
        self._profiles = profile_repository
        self._cards = card_repository

    def _require_published_version(self, version_id: UUID) -> None:
        version = self._versions.get_by_id(version_id)
        if version is None:
            raise ValueError(f"DatasetVersion not found: {version_id}")
        if version.status != DatasetVersionStatus.PUBLISHED:
            raise ValueError("Dataset cards require a PUBLISHED DatasetVersion")

    def create_draft(
        self, version_id: UUID, document: DatasetCardDeclarationsV1
    ) -> DatasetCardRevisionV1:
        self._require_published_version(version_id)
        current = self._cards.get_latest(version_id, lock=True)
        if current is not None and current.lifecycle_status in {"DRAFT", "CONFIRMED"}:
            raise ValueError("An editable dataset card revision already exists")
        return self._cards.add_card(
            version_id, self._cards.next_revision(version_id), document
        )

    def update_draft(
        self, version_id: UUID, request: DatasetCardUpdateRequest
    ) -> DatasetCardRevisionV1:
        self._require_published_version(version_id)
        current = self._cards.get_latest(version_id, lock=True)
        if current is None:
            raise ValueError("Dataset card draft not found")
        if current.revision != request.expected_revision:
            raise CardRevisionConflictError(request.expected_revision, current.revision)
        if current.lifecycle_status != "DRAFT":
            raise ValueError("Only a DRAFT dataset card can be updated")
        created = self._cards.add_card(
            version_id, current.revision + 1, request.document
        )
        self._cards.set_status(version_id, current.revision, "SUPERSEDED")
        return created

    def confirm(self, version_id: UUID, revision: int) -> DatasetCardRevisionV1:
        self._require_published_version(version_id)
        card = self._cards.get_revision(version_id, revision)
        if card is None:
            raise ValueError(f"Dataset card revision not found: {revision}")
        if card.lifecycle_status != "DRAFT":
            raise ValueError("Only a DRAFT dataset card can be confirmed")
        return self._cards.set_status(version_id, revision, "CONFIRMED")

    def publish(self, version_id: UUID, revision: int) -> DatasetCardRevisionV1:
        self._require_published_version(version_id)
        card = self._cards.get_revision(version_id, revision)
        if card is None:
            raise ValueError(f"Dataset card revision not found: {revision}")
        if card.lifecycle_status not in {"DRAFT", "CONFIRMED"}:
            raise ValueError("Only a DRAFT or CONFIRMED dataset card can be published")
        self._cards.supersede_other_cards(version_id, revision)
        return self._cards.set_status(version_id, revision, "PUBLISHED")

    def list_history(self, version_id: UUID) -> list[DatasetCardRevisionV1]:
        self._require_published_version(version_id)
        return self._cards.list_cards(version_id)

    def add_candidate(
        self, version_id: UUID, request: CandidateCreateRequest
    ) -> DatasetProfileFieldCandidateV1:
        self._require_published_version(version_id)
        return self._cards.add_candidate(version_id, request)

    def list_candidates(
        self, version_id: UUID
    ) -> list[DatasetProfileFieldCandidateV1]:
        self._require_published_version(version_id)
        return self._cards.list_candidates(version_id)

    def review_candidate(
        self, version_id: UUID, candidate_id: UUID, request: CandidateReviewRequest
    ) -> DatasetProfileFieldCandidateV1:
        self._require_published_version(version_id)
        return self._cards.review_candidate(
            version_id, candidate_id, request.decision, request.reviewed_by
        )

    def compose_profile(
        self, version_id: UUID, observed_profile_id: UUID | None = None
    ) -> DatasetProfileV1_1:
        self._require_published_version(version_id)
        profiles = self._profiles.list_by_dataset_version(version_id)
        if observed_profile_id is None:
            observed = profiles[0] if profiles else None
        else:
            observed = next(
                (item for item in profiles if item.profile_id == observed_profile_id),
                None,
            )
        if observed is None:
            raise ValueError("Dataset profile has not been generated")

        card = self._cards.get_latest_published(version_id)
        declarations = (
            card.document.model_copy(deep=True)
            if card is not None
            else DatasetCardDeclarationsV1()
        )
        application_source = "not-present"
        if observed.application_context:
            application_source = "migrated-to-declarations"
            if "scenario" not in declarations.application_description:
                declarations.application_description["scenario"] = (
                    observed.application_context
                )
        language = {}
        if observed.content_features is not None:
            language = {
                "distribution": observed.content_features.language_distribution,
                "source": observed.content_features.language_source,
            }
        return DatasetProfileV1_1(
            profile_id=uuid7(),
            dataset_version_id=version_id,
            card=(
                DatasetProfileCardRefV1(
                    card_id=card.card_id,
                    revision=card.revision,
                    lifecycle_status="PUBLISHED",
                    content_sha256=card.content_sha256,
                )
                if card is not None
                else None
            ),
            declarations=declarations,
            observations=DatasetProfileObservationsV1_1(
                profile_id=observed.profile_id,
                profiler=observed.profiler,
                status=observed.status,
                summary=observed.summary,
                identity=observed.identity,
                structure=observed.structure,
                statistics=observed.statistics,
                profiling_metadata=observed.profiling_metadata,
                field_profiles=observed.field_profiles,
                artifacts=observed.artifacts,
                created_at=observed.created_at,
            ),
            provenance=DatasetProfileProvenanceV1_1(
                language=language,
                application_context_source=application_source,
            ),
            created_at=datetime.now(UTC),
        )

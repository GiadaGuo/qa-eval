from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qa_eval.application.cards import CardRevisionConflictError, DatasetCardService
from qa_eval.contracts.cards import (
    CandidateCreateRequest,
    CandidateReviewRequest,
    DatasetCardDeclarationsV1,
    DatasetCardUpdateRequest,
)
from qa_eval.contracts.datasets import DatasetProfileV1
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_card import (
    SQLAlchemyDatasetCardRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_profile import (
    SQLAlchemyDatasetProfileRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)


def _profile(version_id, *, sample_count: int = 3) -> DatasetProfileV1:
    now = datetime.now(UTC)
    return DatasetProfileV1.model_validate(
        {
            "profile_id": uuid4(),
            "dataset_version_id": version_id,
            "profiler": {
                "key": "base_dataset_profiler",
                "version": "1.0.0",
                "config_fingerprint": "sha256:" + "1" * 64,
            },
            "status": "OK",
            "summary": {
                "source_record_count": sample_count,
                "normalized_record_count": sample_count,
                "rejected_record_count": 0,
            },
            "identity": {
                "sample_count": sample_count,
                "format": "jsonl",
                "size_bytes": 100,
            },
            "content_features": {
                "language_distribution": {"zh": 1.0},
                "language_source": "DECLARED",
            },
            "application_context": {"task_types": ["classification"]},
            "field_profiles": [],
            "artifacts": [],
            "created_at": now,
        }
    )


def _service(db_session):
    versions = SQLAlchemyDatasetVersionRepository(db_session)
    profiles = SQLAlchemyDatasetProfileRepository(db_session)
    cards = SQLAlchemyDatasetCardRepository(db_session)
    return DatasetCardService(versions, profiles, cards), profiles


def _published_version(db_session):
    dataset = DatasetModel(id=uuid4(), name="card-test", modality=["text"])
    version = DatasetVersionModel(
        id=uuid4(),
        dataset_id=dataset.id,
        version="v1",
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    db_session.add_all([dataset, version])
    db_session.flush()
    return version


def _document(name: str) -> DatasetCardDeclarationsV1:
    return DatasetCardDeclarationsV1(
        basic_information={"name": name},
        content_characteristics={"modality": "text"},
        construction_process={"source": "test fixture"},
        application_description={},
    )


def test_card_revision_optimistic_lock_publish_and_history_are_immutable(
    db_session,
) -> None:
    version = _published_version(db_session)
    service, profiles = _service(db_session)
    observed = _profile(version.id)
    profiles.add(observed, "a" * 64)

    first = service.create_draft(version.id, _document("first"))
    second = service.update_draft(
        version.id,
        DatasetCardUpdateRequest(
            expected_revision=first.revision, document=_document("second")
        ),
    )
    with pytest.raises(CardRevisionConflictError):
        service.update_draft(
            version.id,
            DatasetCardUpdateRequest(
                expected_revision=first.revision, document=_document("lost-update")
            ),
        )

    service.confirm(version.id, second.revision)
    published = service.publish(version.id, second.revision)
    history = service.list_history(version.id)

    assert published.lifecycle_status == "PUBLISHED"
    assert [item.revision for item in history] == [2, 1]
    assert history[0].document.basic_information["name"] == "second"
    assert history[1].document.basic_information["name"] == "first"
    assert history[1].lifecycle_status == "SUPERSEDED"

    profile_v11 = service.compose_profile(version.id, observed.profile_id)
    assert profile_v11.schema_version == "1.1"
    assert profile_v11.card is not None
    assert profile_v11.card.revision == 2
    assert profile_v11.observations.profile_id == observed.profile_id
    assert profile_v11.declarations.basic_information["name"] == "second"
    assert profile_v11.declarations.application_description["scenario"] == {
        "task_types": ["classification"]
    }
    assert profile_v11.provenance.language["source"] == "DECLARED"

    third = service.create_draft(version.id, _document("third"))
    service.publish(version.id, third.revision)
    final_history = service.list_history(version.id)
    assert [item.revision for item in final_history] == [3, 2, 1]
    assert final_history[1].lifecycle_status == "SUPERSEDED"
    assert final_history[1].document.basic_information["name"] == "second"

    profile_v1 = profiles.get_by_dataset_version(version.id)
    assert profile_v1 is not None
    assert profile_v1.model_dump(mode="json") == observed.model_dump(mode="json")


def test_candidate_is_idempotent_and_reviewed_once(db_session) -> None:
    version = _published_version(db_session)
    service, _ = _service(db_session)
    request = CandidateCreateRequest(
        field_path="$.basic_information.name",
        value="candidate",
        source_type="MANUAL",
        source_ref={"operator": "test"},
    )

    first = service.add_candidate(version.id, request)
    duplicate = service.add_candidate(version.id, request)
    reviewed = service.review_candidate(
        version.id,
        first.candidate_id,
        CandidateReviewRequest(decision="CONFIRMED", reviewed_by="reviewer"),
    )

    assert duplicate.candidate_id == first.candidate_id
    assert reviewed.review_status == "CONFIRMED"
    assert len(service.list_candidates(version.id)) == 1
    with pytest.raises(ValueError, match="already been reviewed"):
        service.review_candidate(
            version.id,
            first.candidate_id,
            CandidateReviewRequest(decision="REJECTED", reviewed_by="reviewer"),
        )


def test_profile_v11_without_card_has_empty_declarations(db_session) -> None:
    version = _published_version(db_session)
    service, profiles = _service(db_session)
    observed = _profile(version.id)
    profiles.add(observed, "b" * 64)

    composed = service.compose_profile(version.id)

    assert composed.card is None
    assert composed.declarations.basic_information == {}
    assert composed.observations.summary.normalized_record_count == 3


def test_dataset_version_delete_removes_card_owned_rows(db_session) -> None:
    version = _published_version(db_session)
    service, _ = _service(db_session)
    service.create_draft(version.id, _document("delete-me"))
    service.add_candidate(
        version.id,
        CandidateCreateRequest(
            field_path="$.basic_information.name",
            value="delete-me",
            source_ref={"test": True},
        ),
    )

    repository = SQLAlchemyDatasetVersionRepository(db_session)
    assert repository.delete(version.id) is True
    assert service._cards.list_cards(version.id) == []
    assert service._cards.list_candidates(version.id) == []

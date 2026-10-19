# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 15:19:17
# @LastEditTime: 2026-10-19 15:27:44
# @Description: 
# ====================================================

import uuid

from sqlalchemy import select

from qa_eval.infrastructure.db.models.dataset import DatasetModel


def test_register_article_dataset(db_session):
    """Register the jhsjk_article logical dataset."""

    dataset = DatasetModel(
        id=uuid.uuid4(),
        name="jhsjk_article",
        description=(
            "Article dataset sourced from MySQL. "
            "One article corresponds to one Sample Record."
        ),
        modality=["text"],
    )

    db_session.add(dataset)
    db_session.flush()

    assert dataset.id is not None
    assert dataset.name == "jhsjk_article"
    assert dataset.modality == ["text"]
    assert dataset.created_at is not None


def test_query_article_dataset(db_session):
    """Query registered article dataset metadata."""

    dataset_id = uuid.uuid4()

    dataset = DatasetModel(
        id=dataset_id,
        name="jhsjk_article",
        description="Article metadata, HTML and plain text",
        modality=["text"],
    )

    db_session.add(dataset)
    db_session.flush()

    result = db_session.scalar(
        select(DatasetModel).where(
            DatasetModel.id == dataset_id
        )
    )

    assert result is not None
    assert result.name == "jhsjk_article"
    assert result.modality == ["text"]
    assert "HTML" in result.description


def test_update_article_dataset_description(db_session):
    """Update logical dataset metadata."""

    dataset = DatasetModel(
        id=uuid.uuid4(),
        name="jhsjk_article",
        description="Initial article dataset description",
        modality=["text"],
    )

    db_session.add(dataset)
    db_session.flush()

    dataset.description = (
        "MySQL article dataset containing article_id, "
        "title, url, pub_date, origin_name, summary, "
        "content, plain_text_content and create_time."
    )

    db_session.flush()

    result = db_session.scalar(
        select(DatasetModel).where(
            DatasetModel.id == dataset.id
        )
    )

    assert result is not None
    assert "plain_text_content" in result.description


def test_commit_does_not_persist_dataset(db_session):
    """A test-level commit must not persist beyond the fixture."""

    dataset = DatasetModel(
        id=uuid.uuid4(),
        name="jhsjk_article_commit_test",
        modality=["text"],
    )

    db_session.add(dataset)
    db_session.commit()

    result = db_session.get(DatasetModel, dataset.id)

    assert result is not None
    assert result.name == "jhsjk_article_commit_test"


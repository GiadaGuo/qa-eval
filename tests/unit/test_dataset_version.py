# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 17:13:30
# @LastEditTime: 2026-10-19 17:13:45
# @Description: 
# ====================================================

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qa_eval.domain.entities.dataset_version import (
    DatasetVersion,
    DatasetVersionStatus,
)


def test_new_version_starts_as_draft():
    version = DatasetVersion(
        dataset_id=uuid4(),
        version="v1",
    )

    assert version.status == DatasetVersionStatus.DRAFT
    assert version.published_at is None


def test_version_can_be_registered_and_published():
    version = DatasetVersion(
        dataset_id=uuid4(),
        version="v1",
    )

    version.mark_registered()

    published_at = datetime.now(UTC)
    version.publish(published_at)

    assert version.status == DatasetVersionStatus.PUBLISHED
    assert version.published_at == published_at


def test_cannot_publish_draft_directly():
    version = DatasetVersion(
        dataset_id=uuid4(),
        version="v1",
    )

    with pytest.raises(ValueError):
        version.publish(datetime.now(UTC))


def test_cannot_register_published_version():
    version = DatasetVersion(
        dataset_id=uuid4(),
        version="v1",
    )

    version.mark_registered()
    version.publish(datetime.now(UTC))

    with pytest.raises(ValueError):
        version.mark_registered()

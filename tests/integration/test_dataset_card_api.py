from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies import get_dataset_card_service, get_dataset_version_service
from apps.api.main import create_app
from qa_eval.application.cards import CardRevisionConflictError


def test_profile_endpoint_preserves_v1_default_and_supports_explicit_v11() -> None:
    version_id = uuid4()
    observed_id = uuid4()
    version_service = Mock()
    version_service.get_profile.return_value.model_dump.return_value = {
        "schema_version": "1.0"
    }
    card_service = Mock()
    card_service.compose_profile.return_value.model_dump.return_value = {
        "schema_version": "1.1"
    }
    app = create_app()
    app.dependency_overrides[get_dataset_version_service] = lambda: version_service
    app.dependency_overrides[get_dataset_card_service] = lambda: card_service

    with TestClient(app) as client:
        legacy = client.get(f"/api/v1/dataset-versions/{version_id}/profile")
        composed = client.get(
            f"/api/v1/dataset-versions/{version_id}/profile",
            params={"schema": "1.1", "observed_profile_id": str(observed_id)},
        )

    assert legacy.json() == {"schema_version": "1.0"}
    assert composed.json() == {"schema_version": "1.1"}
    version_service.get_profile.assert_called_once_with(version_id)
    card_service.compose_profile.assert_called_once_with(version_id, observed_id)


def test_stale_card_update_returns_409_without_silently_overwriting() -> None:
    version_id = uuid4()
    service = Mock()
    service.update_draft.side_effect = CardRevisionConflictError(1, 2)
    app = create_app()
    app.dependency_overrides[get_dataset_card_service] = lambda: service

    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/dataset-versions/{version_id}/card-drafts",
            json={
                "expected_revision": 1,
                "document": {
                    "schema_version": "1.0",
                    "basic_information": {},
                    "content_characteristics": {},
                    "construction_process": {},
                    "application_description": {},
                },
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "CARD_REVISION_CONFLICT",
        "expected_revision": 1,
        "current_revision": 2,
    }

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.application.services.dataset_overview import dataset_overview


def test_v3_overview_without_mapping_and_fingerprint_invalidation():
    version_id = uuid4()
    version = SimpleNamespace(
        id=version_id,
        default_mapping_digest=None,
        artifact_refs={
            "raw_record_manifest": {
                "schema_version": "3.0",
                "dataset_version_id": str(version_id),
                "source_uri": "raw/input.jsonl",
                "source_sha256": "sha256:" + "a" * 64,
                "schema_fingerprint": "sha256:" + "b" * 64,
                "reader": "jsonl-utf8@1",
            },
            "raw_file": {
                "record_count": 321902,
                "fields": [{"name": "title", "data_type": "string", "nullable": False}],
                "preview": [{"title": "excluded from the overview"}],
            },
        },
    )
    session = Mock()
    dataset = SimpleNamespace(modality=["text", "structured"])
    overview = dataset_overview(session, version, dataset)
    assert overview["semantics"] == []
    assert overview["record_count"] == 321902
    assert "preview" not in overview
    context, fingerprint = ApplicabilityService.context_for(session, version, dataset)
    assert fingerprint == overview["fingerprint"]
    assert not context.has_profile
    assert context.metadata_fields == {"title"}
    assert context.artifact_kinds == {"RAW_RECORDS"}
    session.get.assert_not_called()
    session.scalars.assert_not_called()

    version.default_mapping_digest = "sha256:" + "c" * 64
    session.get.return_value = None
    assert dataset_overview(session, version, dataset)["fingerprint"] != fingerprint

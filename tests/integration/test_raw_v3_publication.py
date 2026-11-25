import hashlib
import json
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from fastapi.testclient import TestClient

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from qa_eval.application.evidence.raw_location import RawEvidenceLocation
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def test_opt_in_raw_publication_and_indexed_sample(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'platform.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    def get_session():
        with factory.begin() as session:
            yield session

    app = create_app()
    artifacts = tmp_path / "artifacts"
    store = LocalFileSystemArtifactStore(artifacts)
    app.dependency_overrides[get_db_session] = get_session
    app.dependency_overrides[get_artifact_store] = lambda: store
    source = b'\n{"id":"raw","amount":1.0000000000000000001,"empty":"","null":null}\n'
    source += b'{"id":"second"}\n'
    try:
        with TestClient(app, base_url="http://testserver/api/v1/") as client:
            created = client.post("datasets", json={
                "name": "raw-v3", "owner": "fixture",
                "data_type": "structured", "modality": ["text"],
            })
            assert created.status_code == 201, created.text
            dataset = created.json()
            version = client.post(f"datasets/{dataset['id']}/versions").json()
            path = f"dataset-versions/{version['id']}"
            uploaded = client.post(f"{path}/files", files={"file": ("raw.jsonl", source)})
            assert uploaded.status_code == 200, uploaded.text
            with factory.begin() as session:
                row = session.get(DatasetVersionModel, UUID(version["id"]))
                row.artifact_refs = {
                    **row.artifact_refs,
                    "publication": {"status": "NEEDS_MAPPING"},
                }
            draft = client.get(path).json()
            assert draft["raw_format"] == "jsonl"
            assert draft["publication"]["status"] == "NEEDS_MAPPING"
            publication = client.post(f"{path}/publish", params={"record_schema": "3.0"})
            assert publication.status_code == 200, publication.text
            published_version = client.get(path).json()
            assert published_version["record_schema"] == "3.0"
            assert published_version["publication"] is None
            assert client.get(f"datasets/{dataset['id']}/versions").json()[0]["record_schema"] == "3.0"
            manifest = publication.json()["manifest"]
            assert client.get(path + "/manifest").json() == manifest
            key = uuid5(UUID(version["id"]), f"{manifest['source_sha256']}:1")
            response = client.get(f"{path}/samples/{key}")
            assert response.status_code == 200, response.text
            record = json.loads(response.content, parse_float=Decimal)
            assert record["data"] == json.loads(source.splitlines()[1], parse_float=Decimal)
            assert record["meta"]["line_number"] == 2
            assert record["meta"]["byte_start"] == 1
            assert client.post(f"{path}/publish", params={"record_schema": "3.0"}).status_code == 422
            page = client.get(path + "/samples", params={"limit": 1}).json()
            assert page["total"] == 2
            next_page = client.get(
                path + "/samples", params={"limit": 1, "cursor": page["next_cursor"]}
            ).json()
            assert next_page["next_cursor"] is None
            assert {page["samples"][0]["data"]["id"], next_page["samples"][0]["data"]["id"]} == {
                "raw", "second"
            }
            revision_path = path + "/mapping-revisions"
            mapping = {
                "dataset_version_id": version["id"],
                "schema_fingerprint": manifest["schema_fingerprint"],
                "bindings": [{
                    "semantic": "document.title",
                    "field_tokens": ["id"],
                    "representation": "text/plain",
                }],
            }
            confirmation = client.post(revision_path, json=mapping)
            assert confirmation.status_code == 201, confirmation.text
            old = confirmation.json()
            assert client.post(revision_path, json=mapping).json() == old
            mapping["bindings"][0]["field_tokens"] = ["empty"]
            new = client.post(revision_path, json=mapping).json()
            assert new["digest"] != old["digest"]
            assert client.get(f"{revision_path}/{old['digest']}").json() == old
            assert {item["digest"] for item in client.get(revision_path).json()} == {
                old["digest"], new["digest"]
            }
            workflow = path + "/mapping"
            custom = {
                "key": "custom.amount", "version": "1.0.0", "name": "Amount",
                "description": "Exact source amount", "type": "number",
                "representation": "json",
            }
            assert client.post(workflow + "/semantics", json=custom).status_code == 201
            assert client.post(workflow + "/semantics", json=custom).status_code == 422
            other = client.post("datasets", json={
                "name": "other-v3", "owner": "fixture",
                "data_type": "structured", "modality": ["text"],
            }).json()
            other_version = client.post(f"datasets/{other['id']}/versions").json()["id"]
            other_path = f"dataset-versions/{other_version}"
            assert client.post(
                other_path + "/files", files={"file": ("raw.jsonl", b'{"id":"other"}\n')},
            ).status_code == 200
            assert client.post(
                other_path + "/publish", params={"record_schema": "3.0"},
            ).status_code == 200
            other_semantics = client.get(other_path + "/mapping/semantics").json()
            assert custom["key"] not in {item["key"] for item in other_semantics}
            assert "document.title" in {item["key"] for item in other_semantics}
            seeded = client.put(workflow + "/draft", json={"from_digest": old["digest"]})
            assert seeded.status_code == 200, seeded.text
            assert seeded.json()["items"][0]["confirmed"] is False
            assert client.post(workflow + "/publish", json={}).status_code == 422
            draft = {"items": [
                {"binding": {"semantic": semantic, "field_tokens": [field],
                             "representation": representation}, "confirmed": True}
                for semantic, field, representation in (
                    ("document.title", "id", "text/plain"),
                    ("document.body.html", "empty", "text/html"),
                    ("document.body.plain", "null", "text/plain"),
                    ("custom.amount", "amount", "json"),
                )
            ]}
            current = client.put(workflow + "/draft", json=draft)
            assert current.status_code == 200, current.text
            assert all(not item["confirmed"] for item in current.json()["items"])
            data = {"sample_id": str(key)}
            first_preview = client.post(workflow + "/preview", json=data)
            assert first_preview.status_code == 200, first_preview.text
            observed = json.loads(first_preview.content, parse_float=Decimal)
            assert observed["record"] == record
            values = observed["values"]
            amount = Decimal("1.0000000000000000001")
            assert values["custom.amount:json"]["value"] == amount
            assert values["custom.amount:json"]["raw_value"] == amount
            assert values["document.body.plain:text/plain"]["diagnostics"] == ["NULL"]
            assert values["document.body.html:text/html"]["status"] == "EMPTY"
            diagnostics = client.post(workflow + "/preview", json={**data, "slots": [
                {"name": "wrong_type", "semantic": "document.title",
                 "representation": "text/plain", "value_type": "number"},
                {"name": "unbound", "semantic": "custom.unbound", "representation": "json"},
            ]}).json()["values"]
            assert diagnostics["wrong_type"]["status"] == "INVALID_TYPE"
            assert diagnostics["wrong_type"]["raw_value"] == "raw"
            assert diagnostics["unbound"]["status"] == "UNBOUND"
            second_key = uuid5(
                UUID(version["id"]), f"{manifest['source_sha256']}:{source.index(b'{', 2)}",
            )
            second_preview = client.post(workflow + "/preview", json={"sample_id": str(second_key)})
            assert second_preview.status_code == 200, second_preview.text
            assert second_preview.json()["values"]["custom.amount:json"]["status"] == "MISSING"
            confirmation = {
                "semantic": "custom.amount", "representation": "json", "confirmed": True,
            }
            toggle = workflow + "/draft/confirmation"
            assert client.put(toggle, json=confirmation).json() == client.put(
                toggle, json=confirmation,
            ).json()
            published = client.post(workflow + "/publish", json={"make_default": True})
            assert published.status_code == 201, published.text
            assert len(published.json()["revision"]["bindings"]) == 1
            assert published.json()["default_digest"] == published.json()["digest"]
            assert client.put(toggle, json={**confirmation, "confirmed": False}).status_code == 200
            assert client.post(workflow + "/publish", json={}).status_code == 422
            assert client.post(
                workflow + "/preview", json={**data, "digest": published.json()["digest"]},
            ).json()["values"]["custom.amount:json"]["status"] == "PRESENT"
            with factory.begin() as session:
                MetricRegistryService(session).sync()
            preflight = "quality-contract-versions/input-preflight"
            request = {
                "dataset_version_id": version["id"], "metric_key": "02080104",
                "metric_version": "1.0.0", "calculator_key": "text_completeness",
                "calculator_version": "1.1.0",
            }
            def input_status(**changes):
                response = client.post(preflight, json={**request, **changes})
                assert response.status_code == 200, response.text
                return response.json()["status"]

            assert input_status() == "MAPPING_MISSING"
            assert input_status(mapping_digest=old["digest"]) == "MAPPING_MISSING"
            assert input_status(calculator_version="1.0.0") == "CONTRACT_NOT_MIGRATED"
            incompatible = {
                **mapping, "bindings": [{
                    "semantic": "document.body.plain",
                    "field_tokens": ["amount"], "representation": "text/plain",
                }],
            }
            invalid_digest = client.post(revision_path, json=incompatible).json()["digest"]
            assert input_status(mapping_digest=invalid_digest) == "INPUT_INCOMPATIBLE"
            incompatible["bindings"][0]["field_tokens"] = ["id"]
            valid_digest = client.post(revision_path, json=incompatible).json()["digest"]
            assert input_status(mapping_digest=valid_digest) == "READY"
            manual = {
                "dataset_version_id": version["id"],
                "name": "Raw v3 人工质量契约",
                "version_label": "manual-v1",
                "excluded_metrics": {f"020{i}": "小样本验收范围" for i in range(1, 8)},
                "items": [{
                    **{key: value for key, value in request.items() if key != "dataset_version_id"},
                    "key": "text_completeness",
                    "measurement": "incomplete_text_ratio",
                    "rule": {"operator": "LTE", "threshold": 0.3, "severity": "ERROR", "on_missing": "UNKNOWN"},
                }],
            }
            multi = "quality-contract-versions/multi"
            blocked = client.post(multi, json=manual)
            assert blocked.status_code == 422, blocked.text
            assert "MAPPING_MISSING" in blocked.json()["detail"]
            manual["items"][0]["mapping_digest"] = valid_digest
            contract = client.post(multi, json=manual)
            assert contract.status_code == 201, contract.text
            attributes = contract.json()["contract"]["items"][0]["calculation"]["implementation_constraints"]["attributes"]
            assert attributes["mapping_digest"] == valid_digest
            with factory.begin() as session:
                row = session.get(DatasetVersionModel, UUID(version["id"]))
                row.artifact_refs = {
                    **row.artifact_refs,
                    "raw_record_manifest": {**manifest, "source_uri": "unavailable.jsonl"},
                }
            assert input_status(mapping_digest=valid_digest) == "ARTIFACT_MISSING"
            with factory.begin() as session:
                row = session.get(DatasetVersionModel, UUID(version["id"]))
                row.artifact_refs = {**row.artifact_refs, "raw_record_manifest": manifest}
            stage_id = uuid4()
            evidence_id = RawEvidenceLocation.identity(stage_id, key, "id:1-3")
            assert evidence_id == RawEvidenceLocation.identity(stage_id, key, "id:1-3")
            row = {
                "schema_version": "3.0",
                "row_id": str(evidence_id),
                "sample_id": str(key),
                "content_id": "source:id",
                "location": {
                    "field_tokens": ["id"],
                    "artifact_sha256": manifest["source_sha256"],
                    "text_sha256": "sha256:" + hashlib.sha256(b"raw").hexdigest(),
                    "start": 1, "end": 3,
                },
            }
            with factory() as session:
                locator = EvidenceQueryService(session, store)
                location = locator._resolve_raw_location(UUID(version["id"]), row)
                assert location["precision"] == "EXACT"
                assert location["text"][location["highlight"]["start"]:location["highlight"]["end"]] == "aw"
                row["location"]["artifact_sha256"] = "wrong"
                assert locator._resolve_raw_location(UUID(version["id"]), row)["precision"] == "UNAVAILABLE"
        assert sorted(p.read_bytes() for p in artifacts.rglob("*.jsonl")) == sorted(
            [source, b'{"id":"other"}\n']
        )
    finally:
        engine.dispose()

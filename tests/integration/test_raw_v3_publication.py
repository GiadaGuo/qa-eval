import json
from decimal import Decimal
from uuid import UUID, uuid5

from fastapi.testclient import TestClient

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base
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
            publication = client.post(f"{path}/publish", params={"record_schema": "3.0"})
            assert publication.status_code == 200, publication.text
            manifest = publication.json()["manifest"]
            assert client.get(path + "/manifest").json() == manifest
            key = uuid5(UUID(version["id"]), f"{manifest['source_sha256']}:1")
            response = client.get(f"{path}/samples/{key}")
            assert response.status_code == 200, response.text
            record = json.loads(response.content, parse_float=Decimal)
            assert record["data"] == json.loads(source, parse_float=Decimal)
            assert record["meta"]["line_number"] == 2
            assert record["meta"]["byte_start"] == 1
            assert client.post(f"{path}/publish", params={"record_schema": "3.0"}).status_code == 422
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
        assert [p.read_bytes() for p in artifacts.rglob("*.jsonl")] == [source]
    finally:
        engine.dispose()

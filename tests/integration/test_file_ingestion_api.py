from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset_version import DatasetSourceRowModel
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def test_file_ingestion_publication_and_version_allocation(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'platform.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    def get_session():
        with factory.begin() as session:
            yield session

    app = create_app()
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    app.dependency_overrides[get_db_session] = get_session
    app.dependency_overrides[get_artifact_store] = lambda: store
    mapping = {
        "schema_version": "2.0",
        "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
        "mappings": [
            {"source_path": "$.id", "action": "MAP", "target": {"kind": "SAMPLE_ID"}},
            {
                "source_path": "$.text",
                "action": "MAP",
                "target": {"kind": "CONTENT", "role": "text.body", "modality": "text", "name": "body"},
            },
        ],
    }

    with TestClient(app) as client:
        def request(method, path, status=200, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            assert response.status_code == status, response.text
            return response.json() if response.content else None

        for storage, filename, content in (
            ("file", "records.json", b'[{"id":"a","text":"hello"}]'),
            ("sql", "records.csv", b"id,text\na,hello\n"),
        ):
            dataset = request("POST", "/datasets", 201, json={
                "name": storage, "owner": "研究员", "data_type": "structured", "modality": ["text"],
            })
            ds = f"/datasets/{dataset['id']}"
            first = request("POST", ds + "/versions", 201)
            assert first["version"] == "v1"
            v1 = f"/dataset-versions/{first['id']}"
            upload = {"file": (filename, content)}
            request("POST", v1 + "/files", files=upload)
            request("PUT", v1 + "/field-mapping", json=mapping)
            request("POST", v1 + "/publication", 202, json={"storage": storage})
            assert request("GET", v1 + "/publication") == {"status": "PUBLISHED", "record_count": 1}
            assert request("GET", v1 + "/manifest")["source_snapshot"]["record_count"] == 1
            request("POST", v1 + "/files", 422, files=upload)
            request("PUT", v1 + "/field-mapping", 422, json=mapping)
            assert request("GET", ds + "/source-config")["storage"] == storage

            second = request("POST", ds + "/versions", 201)
            v2 = f"/dataset-versions/{second['id']}"
            request("POST", v2 + "/files", files={"file": ("changed.csv", b"id,body\na,hello\n")})
            assert not request("POST", v2 + "/apply-default-mapping")["applied"]
            request("POST", v2 + "/publication", 422)
            request("POST", v2 + "/files", files=upload)
            assert request("POST", v2 + "/apply-default-mapping")["applied"]
            request("POST", v2 + "/publication", 202)
            assert request("GET", v2 + "/publication")["status"] == "PUBLISHED"

            request("DELETE", ds + "/versions/" + second["id"], 204)
            with ThreadPoolExecutor(3) as executor:
                labels = list(executor.map(lambda _, path=ds: request("POST", path + "/versions", 201)["version"], range(3)))
            assert set(labels) == {"v3", "v4", "v5"}
            request("PUT", ds, json={**dataset, "enabled": False})
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(DatasetSourceRowModel)) == 1
        filtered = request("GET", "/datasets/search?q=研究员&data_type=structured&modality=text&source_kind=file&enabled=false&size=1")
        assert filtered["total"] == 2
        assert len(filtered["items"]) == 1
    engine.dispose()

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from apps.api.routes import dataset_versions
from apps.api.routes.web_session import current_user
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def test_mysql_snapshot_publication_drift_and_retry(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'platform.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    def get_session():
        with factory.begin() as session:
            yield session

    class TestReader:
        table = "articles"
        database = "demo"
        def __init__(self):
            self.connection_config = {"host": "test", "port": 3306}

        content_field = "text"
        failed = False

        def write_snapshot(self, path, on_progress):
            if self.failed:
                raise ValueError("snapshot failed")
            record = {"id": "a", self.content_field: "hello"}
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            return datetime.now(UTC), [
                {"name": name, "data_type": "varchar", "nullable": False, "is_primary_key": name == "id"}
                for name in record
            ]

    reader = TestReader()
    monkeypatch.setattr(dataset_versions, "configured_mysql_reader", lambda table=None: reader)
    app = create_app()
    app.dependency_overrides[current_user] = lambda: {"role": "operator"}
    app.dependency_overrides[get_db_session] = get_session
    app.dependency_overrides[get_artifact_store] = lambda: LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with TestClient(app) as client:
        def request(method, path, status=200, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            assert response.status_code == status, response.text
            return response.json()

        def mapping(field):
            return {
                "schema_version": "2.0",
                "adapter": {"key": "jsonl_mapping", "version": "2.0.0"},
                "mappings": [
                    {"source_path": "$.id", "action": "MAP", "target": {"kind": "SAMPLE_ID"}},
                    {"source_path": f"$.{field}", "action": "MAP",
                     "target": {"kind": "CONTENT", "role": "text.body", "modality": "text"}},
                ],
            }

        dataset = request("POST", "/datasets", 201, json={"name": "Database", "modality": ["text"]})
        endpoint = f"/datasets/{dataset['id']}/mysql-snapshot"

        def new_version(**payload):
            version = request("POST", endpoint, 202, json=payload)
            return version["version_id"], f"/dataset-versions/{version['version_id']}"

        _, first = new_version(table="articles")
        assert request("GET", first + "/publication")["status"] == "NEEDS_MAPPING"
        request("PUT", first + "/field-mapping", json=mapping("text"))
        request("POST", first + "/publication", 202)
        manifest = request("GET", first + "/manifest")
        assert manifest["source_snapshot"]["consistency"] == "MYSQL_INNODB_REPEATABLE_READ"

        _, second = new_version()
        assert request("GET", second + "/publication")["status"] == "PUBLISHED"

        reader.content_field = "body"
        _, third = new_version()
        assert request("GET", third + "/publication")["status"] == "NEEDS_MAPPING"
        request("PUT", third + "/field-mapping", json=mapping("body"))
        request("POST", third + "/publication", 202)
        assert request("GET", third + "/publication")["status"] == "PUBLISHED"
        assert request("GET", first + "/manifest") == manifest

        reader.failed = True
        retry_id, fourth = new_version()
        assert request("GET", fourth + "/publication")["stage"] == "SNAPSHOT"
        assert request("GET", fourth + "/publication")["status"] == "FAILED"
        reader.failed = False
        same_id, _ = new_version(retry_version_id=retry_id)
        assert same_id == retry_id
        assert request("GET", fourth + "/publication")["status"] == "NEEDS_MAPPING"
    engine.dispose()

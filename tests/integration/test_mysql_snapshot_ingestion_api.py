import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from apps.api.routes import dataset_versions
from apps.api.routes.web_session import current_user
from qa_eval.application.normalization.jhsjk_article_normalizer import JhsjkArticleJsonlNormalizer
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


@pytest.mark.parametrize("database_engine", [None, "mysql", "postgresql"])
def test_database_snapshot_publication_drift_and_retry(tmp_path, monkeypatch, database_engine):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'platform.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    def get_session():
        with factory.begin() as session:
            yield session

    class TestReader:
        table = "public.articles" if database_engine == "postgresql" else "articles"
        database = "demo"
        def __init__(self):
            self.connection_config = {"host": "test", "port": 3306}

        content_field = "text"
        failed = False

        def list_snapshot_tables(self):
            return [self.table]

        def get_preview(self, *, limit, readonly):
            assert limit == 5 and readonly
            return {"fields": [{"name": "id"}], "rows": [{"id": "a"}]}

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
    monkeypatch.setattr(dataset_versions, "MySQLDataSourceReader", lambda **kwargs: reader)
    monkeypatch.setattr(dataset_versions, "PostgresDataSourceReader", lambda **kwargs: reader)
    secret_dir = tmp_path / "secrets"
    monkeypatch.setenv("QA_EVAL_SOURCE_SECRET_DIR", str(secret_dir))
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
        endpoint = f"/datasets/{dataset['id']}/database-snapshot"
        password = "never-return-this-value"
        connection = {
            "engine": database_engine, "host": "test", "port": 3306, "database": "demo",
            "user": "user", "password": password,
        }
        if database_engine:
            payload = request("POST", "/database-source/discover", json={"connection": connection, "table": reader.table})
            assert payload["preview"] == [{"id": "a"}]
            invalid = request("POST", "/database-source/discover", 422, json={
                "connection": {**connection, "port": 0},
            })
            assert password not in json.dumps(invalid)


        def new_version(**payload):
            version = request("POST", endpoint, 202, json=payload)
            return version["version_id"], f"/dataset-versions/{version['version_id']}"

        _, first = new_version(table=reader.table, **({"connection": connection} if database_engine else {}))
        if database_engine:
            secret = secret_dir / dataset["id"]
            assert secret.read_text() == password
            assert secret.stat().st_mode & 0o777 == 0o600
            config = request("GET", f"/datasets/{dataset['id']}/source-config")
            assert config["engine"] == database_engine and password not in json.dumps(config)
            with factory() as session:
                assert password not in session.scalar(text("SELECT source_config FROM datasets"))
            request("GET", f"/datasets/{dataset['id']}/database-source/preview")
        assert request("GET", first + "/publication")["status"] == "NEEDS_MAPPING"
        request("PUT", first + "/field-mapping", json=mapping("text"))
        request("POST", first + "/publication", 202)
        manifest = request("GET", first + "/manifest")
        assert manifest["source_snapshot"]["consistency"] == (
            "POSTGRESQL_REPEATABLE_READ" if database_engine == "postgresql" else "MYSQL_INNODB_REPEATABLE_READ"
        )

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

        original_snapshot = reader.write_snapshot

        def delete_during_snapshot(path, on_progress):
            with factory() as session:
                running = session.scalar(
                    select(DatasetVersionModel).order_by(DatasetVersionModel.version.desc())
                )
                running_id = running.id
            deleted = client.delete(f"/api/v1/datasets/{dataset['id']}/versions/{running_id}")
            assert deleted.status_code == 204, deleted.text
            return original_snapshot(path, on_progress)

        monkeypatch.setattr(reader, "write_snapshot", delete_during_snapshot)
        canceled_id, canceled = new_version()
        request("GET", canceled + "/publication", 404)
        assert not (tmp_path / "artifacts" / "datasets" / dataset["id"] / canceled_id).exists()
        monkeypatch.setattr(reader, "write_snapshot", original_snapshot)
        pending_id, pending = new_version()
        request("PUT", pending + "/field-mapping", json=mapping("body"))
        normalize = JhsjkArticleJsonlNormalizer.normalize

        def delete_during_normalization(normalizer, *args, **kwargs):
            deleted = client.delete(f"/api/v1/datasets/{dataset['id']}/versions/{pending_id}")
            assert deleted.status_code == 204, deleted.text
            return normalize(normalizer, *args, **kwargs)

        monkeypatch.setattr(JhsjkArticleJsonlNormalizer, "normalize", delete_during_normalization)
        request("POST", pending + "/publication", 202)
        request("GET", pending + "/publication", 404)
        assert not (tmp_path / "artifacts" / "datasets" / dataset["id"] / pending_id).exists()
    engine.dispose()

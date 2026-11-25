import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qa_eval.config import get_settings
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    normalize_database_url,
)


def test_relative_sqlite_url_is_resolved_from_explicit_root(tmp_path: Path):
    url = normalize_database_url(
        "sqlite:///data/runtime/qa-eval.sqlite3", root_dir=tmp_path
    )

    assert Path(url.database) == tmp_path / "data/runtime/qa-eval.sqlite3"


def test_sqlite_engine_configures_wal_busy_timeout_and_foreign_keys(
    tmp_path: Path,
):
    database_path = tmp_path / "nested" / "qa-eval.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")

    try:
        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA busy_timeout")) == 30_000
            connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE child ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER REFERENCES parent(id)"
                    ")"
                )
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("INSERT INTO child (id, parent_id) VALUES (1, 999)")
            )
    finally:
        engine.dispose()


def test_empty_sqlite_database_migrates_from_zero_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database_path = tmp_path / "qa-eval.sqlite3"
    monkeypatch.setenv("QA_EVAL_DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_config)
        head = script.get_current_head()
        assert head == "d8e52b61a904"
        assert script.get_revision("7d3f74b8e129").down_revision == "238dc116e65c"
        command.upgrade(alembic_config, "head")

        with sqlite3.connect(database_path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == (head,)
            dataset_columns = {
                row[1]: (row[2], row[3])
                for row in connection.execute("PRAGMA table_info(datasets)")
            }
            version_columns = {
                row[1]: (row[2], row[3])
                for row in connection.execute("PRAGMA table_info(dataset_versions)")
            }
            assert dataset_columns["custom_semantics"] == ("JSON", 0)
            assert version_columns["mapping_draft"] == ("JSON", 0)
            assert version_columns["default_mapping_digest"] == ("VARCHAR(71)", 0)
            table_count = connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            assert table_count == 35
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {
                "dataset_cards",
                "dataset_profile_field_candidates",
                "profile_snapshots",
                "calculator_results",
                "calculator_result_artifacts",
            } <= tables

        engine = create_database_engine(f"sqlite:///{database_path}")
        try:
            dataset_id = uuid4()
            with Session(engine) as session:
                session.add(
                    DatasetModel(
                        id=dataset_id,
                        name="sqlite-roundtrip",
                        modality=["text"],
                    )
                )
                session.commit()
                session.expire_all()
                stored = session.get(DatasetModel, dataset_id)
                assert stored is not None
                assert stored.id == dataset_id
                assert stored.modality == ["text"]
        finally:
            engine.dispose()

        command.downgrade(alembic_config, "238dc116e65c")
        with sqlite3.connect(database_path) as connection:
            assert "custom_semantics" not in {
                row[1] for row in connection.execute("PRAGMA table_info(datasets)")
            }
            assert {"mapping_draft", "default_mapping_digest"}.isdisjoint(
                row[1]
                for row in connection.execute("PRAGMA table_info(dataset_versions)")
            )

        command.upgrade(alembic_config, "head")
        engine = create_database_engine(f"sqlite:///{database_path}")
        try:
            with Session(engine) as session:
                stored = session.get(DatasetModel, dataset_id)
                assert stored is not None
                assert stored.name == "sqlite-roundtrip"
                assert stored.custom_semantics is None
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()

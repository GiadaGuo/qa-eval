import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
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
    monkeypatch.setenv(
        "QA_EVAL_DATABASE_URL", f"sqlite:///{database_path}"
    )
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        with sqlite3.connect(database_path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("c9f4a21d8e73",)
            table_count = connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            assert table_count == 27

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
    finally:
        get_settings.cache_clear()

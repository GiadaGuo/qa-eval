"""Database engine and session factories."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from qa_eval.config import get_settings
from qa_eval.paths import PROJECT_ROOT


def normalize_database_url(database_url: str, *, root_dir: Path = PROJECT_ROOT) -> URL:
    """Resolve relative SQLite files against the project root."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = (root_dir / database_path).resolve()
    return url.set(database=str(database_path))


def create_database_engine(
    database_url: str,
    *,
    root_dir: Path = PROJECT_ROOT,
) -> Engine:
    """Create an engine with dialect-appropriate connection settings."""
    url = normalize_database_url(database_url, root_dir=root_dir)

    if url.get_backend_name() == "sqlite":
        if url.database not in (None, "", ":memory:"):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        database_engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(database_engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

        return database_engine

    return create_engine(url, pool_pre_ping=True)


def create_session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=database_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


settings = get_settings()
engine = create_database_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session

"""Database column types shared by SQLite and PostgreSQL."""

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB as PostgreSQLJSONB
from sqlalchemy.ext.compiler import compiles

# Keep JSONB on PostgreSQL while presenting SQLAlchemy's portable JSON type to
# SQLite and other dialects.  The compatibility names minimize model churn.
JSONB = JSON().with_variant(PostgreSQLJSONB(), "postgresql")
UUID = Uuid


@compiles(PostgreSQLJSONB, "sqlite")
def compile_postgresql_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    """Allow historical PostgreSQL migrations to create JSON columns on SQLite."""
    return "JSON"


def json_document_type():
    """Return a fresh portable JSON type for Alembic migration columns."""
    return JSON().with_variant(PostgreSQLJSONB(), "postgresql")

import os

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from qa_eval.config import get_settings
from qa_eval.infrastructure.db.session import engine
from qa_eval.paths import resolve_project_path, safe_path_label

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    database: "DatabaseHealth"
    artifact_store: "ArtifactStoreHealth"


class DatabaseHealth(BaseModel):
    dialect: str
    writable: bool


class ArtifactStoreHealth(BaseModel):
    type: str
    root: str
    writable: bool


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    settings = get_settings()
    database_writable = False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if connection.dialect.name == "sqlite":
                database_writable = (
                    connection.scalar(text("PRAGMA query_only")) == 0
                )
            elif connection.dialect.name == "postgresql":
                database_writable = (
                    connection.scalar(text("SHOW transaction_read_only")) == "off"
                )
    except SQLAlchemyError:
        database_writable = False

    artifact_type = settings.artifact_store_type
    artifact_root = "<remote>"
    artifact_writable = bool(settings.artifact_store_s3_bucket)
    if artifact_type == "local":
        resolved_root = resolve_project_path(settings.artifact_store_local_root)
        artifact_root = safe_path_label(resolved_root)
        try:
            resolved_root.mkdir(parents=True, exist_ok=True)
            artifact_writable = os.access(resolved_root, os.W_OK)
        except OSError:
            artifact_writable = False

    healthy = database_writable and artifact_writable
    return HealthResponse(
        status="ok" if healthy else "degraded",
        service="qa-eval-api",
        database=DatabaseHealth(
            dialect=engine.dialect.name,
            writable=database_writable,
        ),
        artifact_store=ArtifactStoreHealth(
            type=artifact_type,
            root=artifact_root,
            writable=artifact_writable,
        ),
    )

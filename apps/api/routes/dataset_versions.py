# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 闭环 2：Dataset Version 上传、发布、Manifest、Sample 预览与 Profile API。
# ====================================================

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from mysql.connector import Error as MySQLError
from psycopg import Error as PostgresError
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import insert, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from apps.api.dependencies import (
    configured_mysql_reader,
    get_artifact_store,
    get_dataset_card_service,
    get_dataset_version_service,
    get_db_session,
    get_profile_snapshot_service,
    get_quality_result_service,
)
from apps.api.routes.web_session import current_user
from qa_eval.application.artifact_jsonl import read_jsonl_page
from qa_eval.application.cards import CardRevisionConflictError, DatasetCardService
from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
    IndexNotReadyError,
)
from qa_eval.application.normalization.record_reader import (
    RawRecordManifest,
    RawSampleRecord,
)
from qa_eval.application.normalization.source_readers import source_reader
from qa_eval.application.services.dataset_version_service import (
    DatasetVersionService,
)
from qa_eval.application.services.mapping_revision_service import MappingRevisionService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.application.snapshots import ProfileSnapshotService
from qa_eval.contracts.cards import (
    CandidateCreateRequest,
    CandidateReviewRequest,
    DatasetCardDeclarationsV1,
    DatasetCardUpdateRequest,
)
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.contracts.logical_inputs import MappingRevision
from qa_eval.domain.exceptions import DatasetVersionInUseError
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import (
    DatasetSourceRowModel,
    DatasetVersionModel,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.source_secrets import (
    load_source_password,
    save_source_password,
)
from qa_eval.infrastructure.mysql.datasource_reader import MySQLDataSourceReader
from qa_eval.infrastructure.postgresql.datasource_reader import PostgresDataSourceReader

router = APIRouter(prefix="/api/v1", tags=["dataset-versions"])


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=64)


class VersionResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    version: str
    status: str
    created_at: str | None
    published_at: str | None
    has_raw_file: bool
    has_field_mapping: bool
    record_count: int | None = None
    snapshot_at: str | None = None
    publication: dict | None = None


class ProfileSnapshotCreate(BaseModel):
    rule_set_refs: list[str] = Field(default_factory=list)
    calculator_bindings: list[dict] = Field(default_factory=list)


def _version_response(version, service: DatasetVersionService, session: Session | None = None) -> VersionResponse:
    setup = service.get_setup_status(version.id)
    row = session.get(DatasetVersionModel, version.id) if session is not None else None
    refs = row.artifact_refs or {} if row else {}
    raw = refs.get("raw_file", {})
    return VersionResponse(
        record_count=raw.get("record_count"),
        snapshot_at=raw.get("captured_at"),
        publication=refs.get("publication"),
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        status=version.status.value,
        created_at=version.created_at.isoformat() if version.created_at else None,
        published_at=version.published_at.isoformat() if version.published_at else None,
        **setup,
    )


@router.post(
    "/datasets/{dataset_id}/versions", response_model=VersionResponse, status_code=201
)
def create_dataset_version(
    dataset_id: UUID,
    payload: VersionCreate | None = None,
    session: Session = Depends(get_db_session),
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        repository = SQLAlchemyDatasetRepository(session)
        if repository.get_by_id(dataset_id) is None:
            raise ValueError("数据集不存在")
        if payload is None:
            number = repository.allocate_version_number(dataset_id)
            version_label = f"v{number}"
        else:
            version_label = payload.version_label
            if version_label.startswith("v") and version_label[1:].isascii() and version_label[1:].isdigit():
                repository.reserve_version_number(dataset_id, int(version_label[1:]))
        version = service.create_version(dataset_id, version_label)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _version_response(version, service)


@router.get("/datasets/{dataset_id}/versions", response_model=list[VersionResponse])
def list_dataset_versions(
    dataset_id: UUID,
    session: Session = Depends(get_db_session),
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    versions = service.list_versions(dataset_id)
    return [_version_response(version, service, session) for version in versions]


class VersionDeletionConfirmation(BaseModel):
    contract_version_ids: set[UUID]


@router.get("/datasets/{dataset_id}/versions/{version_id}/deletion-preview")
def preview_dataset_version_deletion(
    dataset_id: UUID,
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    preview = service.preview_deletion(dataset_id, version_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="DatasetVersion not found")
    return preview


@router.delete("/datasets/{dataset_id}/versions/{version_id}", status_code=204)
def delete_dataset_version(
    dataset_id: UUID,
    version_id: UUID,
    confirmation: VersionDeletionConfirmation | None = None,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        deleted = (
            service.delete_version(dataset_id, version_id)
            if confirmation is None else service.delete_version(
                dataset_id, version_id, confirmed_contract_ids=confirmation.contract_version_ids
            )
        )
    except DatasetVersionInUseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "dependencies": exc.dependencies,
            },
        ) from exc
    except ValueError as exc:
        # 版本存在但不属于指定数据集，与不存在同样返回 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="DatasetVersion not found",
        )


@router.post("/dataset-versions/{version_id}/files")
def upload_raw_file(
    version_id: UUID,
    file: Annotated[UploadFile, File()],
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    if file.filename is None or not file.filename.lower().endswith(
        (".jsonl", ".json", ".parquet", ".csv")
    ):
        raise HTTPException(
            status_code=422,
            detail="Only .jsonl, .json, .parquet and .csv files are supported",
        )

    suffix = Path(file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        upload_path = Path(tmp.name)
    try:
        result = service.upload_raw_path(version_id, file.filename, upload_path)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        upload_path.unlink(missing_ok=True)

    return result


@router.put(
    "/dataset-versions/{version_id}/field-mapping",
    response_model=FieldMappingSpecV2,
)
def save_field_mapping(
    version_id: UUID,
    mapping: FieldMappingSpecV2,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        return service.save_field_mapping(version_id, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/dataset-versions/{version_id}/field-mapping",
    response_model=FieldMappingSpecV2 | None,
)
def get_field_mapping(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    return service.get_field_mapping(version_id)


@router.get("/dataset-versions/{version_id}/source-inspection")
def inspect_uploaded_source(
    version_id: UUID,
    limit: int = 5,
    max_text_length: int = 300,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if max_text_length < 1 or max_text_length > 5000:
        raise HTTPException(
            status_code=422,
            detail="max_text_length must be between 1 and 5000",
        )
    try:
        return service.inspect_uploaded_source(
            version_id,
            preview_limit=limit,
            max_text_length=max_text_length,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _lock_version(session: Session, version_id: UUID) -> DatasetVersionModel:
    # A write lock also serializes requests on SQLite, where FOR UPDATE is ignored.
    session.execute(
        update(DatasetVersionModel)
        .where(DatasetVersionModel.id == version_id)
        .values(status=DatasetVersionModel.status)
    )
    row = session.get(DatasetVersionModel, version_id, populate_existing=True)
    if row is None:
        raise HTTPException(status_code=404, detail="数据集版本不存在")
    return row


class FilePublicationRequest(BaseModel):
    storage: Literal["file", "sql"] | None = None


def _persist_sql_snapshot(session, artifact_store, version_id, raw):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "raw"
        artifact_store.materialize(raw["uri"], path)
        batch = []
        for row_number, values in source_reader(path, raw["format"]).iter_rows():
            batch.append({"version_id": version_id, "row_number": row_number, "values": dict(values)})
            if len(batch) == 1000:
                session.execute(insert(DatasetSourceRowModel), batch)
                batch.clear()
        if batch:
            session.execute(insert(DatasetSourceRowModel), batch)


class PublicationCanceled(Exception):
    pass


def _check_publication(factory, version_id):
    with factory() as session:
        if session.get(DatasetVersionModel, version_id) is None:
            raise PublicationCanceled


def _cleanup_canceled_publication(artifact_store, dataset_id, version_id):
    try:
        artifact_store.delete_prefix(f"datasets/{dataset_id}/{version_id}/")
    except Exception:
        logging.getLogger(__name__).exception("Canceled publication cleanup failed for %s", version_id)


def _publish_file_version(factory, artifact_store: ArtifactStore, version_id: UUID) -> None:
    dataset_id = None
    try:
        with factory.begin() as session:
            service = get_dataset_version_service(session, artifact_store)
            row = session.get(DatasetVersionModel, version_id)
            if row is None or (row.artifact_refs or {}).get("publication", {}).get("status") != "RUNNING":
                return
            dataset_id = row.dataset_id
            raw = row.artifact_refs["raw_file"]
            dataset = session.get(DatasetModel, row.dataset_id)
            mysql = dataset.source_kind == "database"
            if mysql and not raw.get("source_snapshot"):
                raise ValueError("数据库版本必须使用一致性快照")
            storage = row.artifact_refs["publication"]["storage"]
            last_check = monotonic()

            def check_canceled():
                nonlocal last_check
                now = monotonic()
                if now - last_check >= 1:
                    _check_publication(factory, version_id)
                    last_check = now

            def before_publish():
                _check_publication(factory, version_id)
                try:
                    _lock_version(session, version_id)
                except HTTPException as exc:
                    raise PublicationCanceled from exc
                if storage == "sql":
                    _persist_sql_snapshot(session, artifact_store, version_id, raw)

            service.publish_version(
                version_id, background=True, check_canceled=check_canceled,
                before_publish=before_publish,
            )
            if not mysql:
                dataset.source_kind = "file"
                dataset.storage_path = (
                    f"sql://dataset_source_rows/{dataset.id}"
                    if storage == "sql" else f"datasets/{dataset.id}/"
                )
            config = dataset.source_config or {}
            if not config.get("mapping"):
                dataset.source_config = {
                    **config,
                    "storage": storage,
                    "fields": (raw.get("source_snapshot") or {}).get("fields", raw["fields"]),
                    "mapping": row.artifact_refs["field_mapping"],
                    "reference_version_id": str(version_id),
                }
    except PublicationCanceled:
        if dataset_id:
            _cleanup_canceled_publication(artifact_store, dataset_id, version_id)
    except Exception as exc:
        logging.getLogger(__name__).exception("Dataset publication failed for %s", version_id)
        with factory.begin() as session:
            row = session.get(DatasetVersionModel, version_id)
            if row is None and dataset_id:
                _cleanup_canceled_publication(artifact_store, dataset_id, version_id)
            if row is not None and row.status == "DRAFT":
                reason = str(exc) if isinstance(exc, ValueError) else "发布失败，请检查服务日志"
                row.artifact_refs = {
                    **row.artifact_refs,
                    "publication": {"status": "FAILED", "reason": reason},
                }


class DatabaseConnection(BaseModel):
    engine: Literal["mysql", "postgresql"]
    host: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$", max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)

    def reader(self, table: str | None = None):
        fields = self.model_dump(exclude={"engine", "password"})
        password = self.password.get_secret_value()
        if self.engine == "mysql":
            return MySQLDataSourceReader(**fields, password=password, table=table or "_")
        if table is not None and (table.count(".") != 1 or not all(table.split("."))):
            raise ValueError("PostgreSQL 目标表需要包含 Schema")
        return PostgresDataSourceReader(**fields, password=password, table=table)


class DatabaseDiscoveryRequest(BaseModel):
    connection: DatabaseConnection
    table: str | None = Field(default=None, min_length=1, max_length=129)


@router.post("/database-source/discover", dependencies=[Depends(current_user)])
def discover_database_source(payload: DatabaseDiscoveryRequest):
    try:
        reader = payload.connection.reader(payload.table)
        tables = reader.list_snapshot_tables()
        if payload.table is None:
            return {"tables": tables}
        if payload.table not in tables:
            raise HTTPException(status_code=422, detail="请选择可访问的目标表")
        preview = reader.get_preview(limit=5, readonly=True)
        return {"tables": tables, "table": payload.table, "fields": preview["fields"], "preview": preview["rows"]}
    except (MySQLError, PostgresError, OSError):
        raise HTTPException(status_code=502, detail="数据库连接或探测失败，请检查地址、账号及读取权限") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="数据库名或目标表格式无效") from None


class DatabaseSnapshotRequest(BaseModel):
    table: str | None = Field(default=None, min_length=1, max_length=129)
    connection: DatabaseConnection | None = None
    retry_version_id: UUID | None = None


@router.get("/managed-mysql", dependencies=[Depends(current_user)])
def discover_managed_mysql(table: str | None = Query(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)):
    reader = configured_mysql_reader(table)
    try:
        tables = reader.list_snapshot_tables()
        if table:
            if table not in tables:
                raise HTTPException(status_code=422, detail="请选择可访问的 InnoDB 表")
            preview = reader.get_preview(limit=5, readonly=True)
            return {
                "tables": tables,
                "table": table,
                "preview": preview["rows"],
                "fields": preview.get("fields", []),
            }
        return {"tables": tables, "table": reader.table}
    except MySQLError as exc:
        raise HTTPException(status_code=502, detail=f"MySQL 连接或探测失败（{exc.errno}）") from None


def _mysql_source_identity(reader):
    return {"host": reader.connection_config["host"], "port": reader.connection_config["port"], "database": reader.database}


def _database_reader(config: dict):
    if credential_ref := config.get("credential_ref"):
        return DatabaseConnection(
            engine=config["engine"],
            **config["connection"],
            password=load_source_password(UUID(credential_ref)),
        ).reader(config["table"])
    reader = configured_mysql_reader(config["table"])
    if _mysql_source_identity(reader) != config["connection"]:
        raise ValueError("MySQL 连接目标已变更，请重新配置数据源")
    return reader


@router.get("/datasets/{dataset_id}/database-source/preview", dependencies=[Depends(current_user)])
def probe_saved_database(dataset_id: UUID, session: Session = Depends(get_db_session)):
    dataset = session.get(DatasetModel, dataset_id)
    if dataset is None or dataset.source_kind != "database" or not dataset.source_config:
        raise HTTPException(status_code=404, detail="数据库数据源不存在")
    try:
        preview = _database_reader(dataset.source_config).get_preview(limit=5, readonly=True)
        return {"fields": preview["fields"], "preview": preview["rows"]}
    except (MySQLError, PostgresError, OSError, ValueError):
        raise HTTPException(status_code=502, detail="已保存的数据库连接无法读取，请检查配置及读取权限") from None


def _build_database_snapshot(factory, artifact_store, version_id: UUID, config: dict, dataset_id: UUID) -> None:
    publish = False
    try:
        _check_publication(factory, version_id)
        engine = config.get("engine", "mysql")
        reader = _database_reader(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{engine}-snapshot.jsonl"
            last_update = monotonic()

            def report_progress(count: int) -> None:
                nonlocal last_update
                now = monotonic()
                if now - last_update < 2:
                    return
                last_update = now
                with factory.begin() as session:
                    row = session.get(DatasetVersionModel, version_id)
                    if row is None:
                        raise PublicationCanceled
                    refs = row.artifact_refs or {}
                    row.artifact_refs = {
                        **refs,
                        "publication": {**refs["publication"], "record_count": count},
                    }

            captured_at, fields = reader.write_snapshot(path, report_progress)
            _check_publication(factory, version_id)
            with factory.begin() as session:
                service = get_dataset_version_service(session, artifact_store)
                uploaded = service.upload_raw_path(version_id, path.name, path, source_snapshot={
                    "uri": f"{engine}://{reader.database}/{reader.table}",
                    "source_type": f"{engine}_snapshot",
                    "consistency": (
                        "MYSQL_INNODB_REPEATABLE_READ" if engine == "mysql"
                        else "POSTGRESQL_REPEATABLE_READ"
                    ),
                    "captured_at": captured_at.isoformat(),
                    "fields": fields,
                })
                row = session.get(DatasetVersionModel, version_id)
                mapping = config.get("mapping")
                previous = {field["name"]: field for field in config.get("fields", [])}
                current = {field["name"]: field for field in fields}
                compatible = all(
                    name in current
                    and current[name]["data_type"] == field["data_type"]
                    and (field["nullable"] or not current[name]["nullable"])
                    for name, field in previous.items()
                )
                publication = {
                    "status": "NEEDS_MAPPING",
                    "record_count": uploaded["record_count"],
                    "reason": "Schema 不兼容，需要调整映射" if mapping else "请确认初始字段映射",
                }
                row.artifact_refs = {**row.artifact_refs, "publication": publication}
                session.flush()
                if mapping and compatible:
                    service.save_field_mapping(version_id, FieldMappingSpecV2.model_validate(mapping))
                    row.artifact_refs = {
                        **row.artifact_refs,
                        "publication": {"status": "RUNNING", "stage": "NORMALIZING", "storage": "file",
                                        "record_count": uploaded["record_count"]},
                    }
                    publish = True
        if publish:
            _publish_file_version(factory, artifact_store, version_id)
    except PublicationCanceled:
        _cleanup_canceled_publication(artifact_store, dataset_id, version_id)
    except Exception as exc:  # noqa: BLE001 - record any worker failure without exposing connection details
        reason = (
            f"MySQL 快照读取失败（{exc.errno}）" if isinstance(exc, MySQLError)
            else str(exc) if isinstance(exc, ValueError) else "数据库快照生成失败，请检查连接及读取权限"
        )
        with factory.begin() as session:
            row = session.get(DatasetVersionModel, version_id)
            if row is None:
                _cleanup_canceled_publication(artifact_store, dataset_id, version_id)
            elif row.status == "DRAFT":
                row.artifact_refs = {
                    **(row.artifact_refs or {}),
                    "publication": {"status": "FAILED", "stage": "SNAPSHOT", "reason": reason},
                }


@router.post("/datasets/{dataset_id}/database-snapshot", status_code=202, dependencies=[Depends(current_user)])
@router.post("/datasets/{dataset_id}/mysql-snapshot", status_code=202, dependencies=[Depends(current_user)])
def start_database_snapshot(
    dataset_id: UUID,
    payload: DatabaseSnapshotRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
):
    session.execute(update(DatasetModel).where(DatasetModel.id == dataset_id).values(enabled=DatasetModel.enabled))
    dataset = session.get(DatasetModel, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    config = dataset.source_config or {}
    if dataset.source_kind is None and payload.table:
        connection = payload.connection
        try:
            reader = connection.reader(payload.table) if connection else configured_mysql_reader(payload.table)
        except ValueError:
            raise HTTPException(status_code=422, detail="目标表格式无效") from None
        engine = connection.engine if connection else "mysql"
        config = {
            "table": reader.table,
            "connection": connection.model_dump(exclude={"engine", "password"}) if connection else _mysql_source_identity(reader),
            "engine": engine,
            "storage": "file",
        }
        if connection:
            try:
                save_source_password(dataset_id, connection.password.get_secret_value())
            except OSError:
                raise HTTPException(status_code=503, detail="数据库凭证无法安全保存") from None
            config["credential_ref"] = str(dataset_id)
        dataset.source_kind = "database"
        dataset.storage_path = f"{engine}://{reader.database}/{reader.table}"
        dataset.source_config = config
    elif dataset.source_kind != "database" or (payload.table and config.get("table") != payload.table):
        raise HTTPException(status_code=409, detail="数据源配置与请求不匹配")
    elif payload.connection:
        raise HTTPException(status_code=409, detail="此数据集已配置数据库连接")
    if payload.retry_version_id:
        row = _lock_version(session, payload.retry_version_id)
        if row.dataset_id != dataset_id or row.status != "DRAFT":
            raise HTTPException(status_code=404, detail="可重试版本不存在")
        previous = (row.artifact_refs or {}).get("publication", {})
        if previous.get("status") == "RUNNING":
            return {**previous, "version_id": row.id}
        if previous.get("status") != "FAILED" or previous.get("stage") != "SNAPSHOT":
            raise HTTPException(status_code=409, detail="此版本无需重新读取快照")
    else:
        number = SQLAlchemyDatasetRepository(session).allocate_version_number(dataset_id)
        service = get_dataset_version_service(session, artifact_store)
        version = service.create_version(dataset_id, f"v{number}")
        row = session.get(DatasetVersionModel, version.id)
    publication = {"status": "RUNNING", "stage": "SNAPSHOT"}
    row.artifact_refs = {**(row.artifact_refs or {}), "publication": publication}
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    session.commit()
    background.add_task(_build_database_snapshot, factory, artifact_store, row.id, config, dataset_id)
    return {**publication, "version_id": row.id}


@router.post("/dataset-versions/{version_id}/apply-default-mapping")
def apply_default_file_mapping(
    version_id: UUID,
    session: Session = Depends(get_db_session),
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    row = _lock_version(session, version_id)
    raw = (row.artifact_refs or {}).get("raw_file")
    if row.status != "DRAFT" or not raw:
        raise HTTPException(status_code=422, detail="请先上传原始文件")
    dataset = session.get(DatasetModel, row.dataset_id)
    config = dataset.source_config or {}
    if dataset.source_kind == "database":
        raise HTTPException(status_code=409, detail="数据库版本由快照流程检查 Schema")
    if not config.get("mapping"):
        return {"applied": False, "reason": "需要确认初始字段映射"}
    previous = {field["name"]: field for field in config["fields"]}
    current = {field["name"]: field for field in raw["fields"]}
    if not all(
        name in current
        and current[name]["data_type"] == field["data_type"]
        and (field["nullable"] or not current[name]["nullable"])
        for name, field in previous.items()
    ):
        return {"applied": False, "reason": "Schema 不兼容，需要调整字段映射"}
    service.save_field_mapping(version_id, FieldMappingSpecV2.model_validate(config["mapping"]))
    return {"applied": True}


@router.post("/dataset-versions/{version_id}/publication", status_code=202)
def start_file_publication(
    version_id: UUID,
    background: BackgroundTasks,
    payload: FilePublicationRequest | None = None,
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
):
    row = _lock_version(session, version_id)
    refs = row.artifact_refs or {}
    if row.status == "PUBLISHED":
        return {"status": "PUBLISHED"}
    if refs.get("publication", {}).get("status") == "RUNNING":
        return refs["publication"]
    if row.status != "DRAFT" or not refs.get("raw_file") or not refs.get("field_mapping"):
        raise HTTPException(status_code=422, detail="发布前需要配置原始文件和字段映射")
    dataset = session.get(DatasetModel, row.dataset_id)
    if dataset.source_kind == "database" and not refs["raw_file"].get("source_snapshot"):
        raise HTTPException(status_code=422, detail="数据库版本必须使用一致性快照")
    configured = (dataset.source_config or {}).get("storage")
    requested = payload.storage if payload else None
    if configured and requested and configured != requested:
        raise HTTPException(status_code=422, detail="版本存储方式必须与数据源配置一致")
    publication = {
        "status": "RUNNING",
        "stage": "NORMALIZING",
        "record_count": refs["raw_file"].get("record_count"),
        "storage": configured or requested or "file",
    }
    row.artifact_refs = {**refs, "publication": publication}
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    session.commit()
    background.add_task(_publish_file_version, factory, artifact_store, version_id)
    return publication


@router.get("/dataset-versions/{version_id}/publication")
def get_file_publication(version_id: UUID, session: Session = Depends(get_db_session)):
    row = session.get(DatasetVersionModel, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据集版本不存在")
    refs = row.artifact_refs or {}
    return {
        "record_count": refs.get("raw_file", {}).get("record_count"),
        **refs.get("publication", {"status": row.status}),
    }


@router.post("/dataset-versions/{version_id}/publish")
def publish_dataset_version(
    version_id: UUID,
    record_schema: Literal["2.0", "3.0"] = "2.0",
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        result = (
            service.publish_raw_version(version_id)
            if record_schema == "3.0"
            else service.publish_version(version_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result


@router.post("/dataset-versions/{version_id}/mapping-revisions", status_code=201)
def confirm_mapping_revision(
    version_id: UUID,
    revision: MappingRevision,
    session: Session = Depends(get_db_session),
):
    if revision.dataset_version_id != version_id:
        raise HTTPException(status_code=422, detail="Mapping revision uses another DatasetVersion")
    try:
        digest = MappingRevisionService(session).confirm(revision)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"digest": digest, "revision": revision.model_dump(mode="json")}


@router.get("/dataset-versions/{version_id}/mapping-revisions")
def list_mapping_revisions(
    version_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
):
    if session.get(DatasetVersionModel, version_id) is None:
        raise HTTPException(status_code=404, detail="DatasetVersion not found")
    return MappingRevisionService(session).list(version_id, limit=limit, offset=offset)


@router.get("/dataset-versions/{version_id}/mapping-revisions/{digest}")
def get_mapping_revision(
    version_id: UUID,
    digest: str,
    session: Session = Depends(get_db_session),
):
    revision = MappingRevisionService(session).get(version_id, digest)
    if revision is None:
        raise HTTPException(status_code=404, detail="Mapping revision not found")
    return {"digest": digest, "revision": revision.model_dump(mode="json")}


@router.get("/dataset-versions/{version_id}")
def get_dataset_version(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    version = service.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="DatasetVersion not found")

    return _version_response(version, service)


@router.get("/dataset-versions/{version_id}/manifest")
def get_dataset_version_manifest(
    version_id: UUID,
    service: DatasetVersionService = Depends(get_dataset_version_service),
):
    try:
        manifest = service.get_manifest(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return manifest.model_dump(mode="json")


@router.get("/dataset-versions/{version_id}/samples")
def get_dataset_version_samples(
    version_id: UUID,
    limit: int = Query(default=50, ge=1, le=50),
    cursor: str | None = None,
    service: DatasetVersionService = Depends(get_dataset_version_service),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
):
    try:
        manifest = service.get_manifest(version_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if isinstance(manifest, RawRecordManifest):
        spec = ArtifactIndexSpec(
            manifest.source_uri, manifest.source_sha256, "sample_id", "SAMPLE", manifest
        )
        indexes = ArtifactRecordIndexService(artifact_store)
        try:
            samples, next_cursor = indexes.query(spec, limit=limit, cursor=cursor)
        except IndexNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = json.dumps({
            "total": indexes.status(spec)["record_count"],
            "limit": limit,
            "next_cursor": next_cursor,
        }).encode()
        rows = b",".join(sample.response_json() for sample in samples)
        return Response(payload[:-1] + b',"samples":[' + rows + b"]}", media_type="application/json")

    normalized_artifact = next(
        (a for a in manifest.artifacts if a.kind == "NORMALIZED_DATA"),
        None,
    )
    if normalized_artifact is None:
        raise HTTPException(status_code=404, detail="Normalized artifact not found")

    samples = read_jsonl_page(
        artifact_store,
        normalized_artifact.uri,
        offset=0,
        limit=limit,
    )

    return {
        "total": normalized_artifact.record_count,
        "limit": limit,
        "offset": 0,
        "samples": samples,
    }


@router.get("/dataset-versions/{version_id}/samples/{sample_id}")
def get_dataset_version_sample_by_id(
    version_id: UUID,
    sample_id: str,
    quality_service: QualityResultService = Depends(get_quality_result_service),
):
    try:
        sample = quality_service.get_sample_by_id(version_id, sample_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if sample is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    if isinstance(sample, RawSampleRecord):
        return Response(sample.response_json(), media_type="application/json")
    return sample.model_dump(mode="json")


@router.get("/dataset-versions/{version_id}/profile")
def get_dataset_version_profile(
    version_id: UUID,
    schema: Literal["1.0", "1.1"] = "1.0",
    observed_profile_id: UUID | None = None,
    service: DatasetVersionService = Depends(get_dataset_version_service),
    card_service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        profile = (
            service.get_profile(version_id)
            if schema == "1.0"
            else card_service.compose_profile(version_id, observed_profile_id)
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return profile.model_dump(mode="json")


@router.post("/dataset-versions/{version_id}/card-drafts", status_code=201)
def create_dataset_card_draft(
    version_id: UUID,
    document: DatasetCardDeclarationsV1,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.create_draft(version_id, document).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/dataset-versions/{version_id}/card-drafts")
def update_dataset_card_draft(
    version_id: UUID,
    request: DatasetCardUpdateRequest,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.update_draft(version_id, request).model_dump(mode="json")
    except CardRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CARD_REVISION_CONFLICT",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dataset-versions/{version_id}/cards/{revision}:confirm")
def confirm_dataset_card(
    version_id: UUID,
    revision: int,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.confirm(version_id, revision).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dataset-versions/{version_id}/cards/{revision}:publish")
def publish_dataset_card(
    version_id: UUID,
    revision: int,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.publish(version_id, revision).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/dataset-versions/{version_id}/cards")
def list_dataset_card_history(
    version_id: UUID,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return [item.model_dump(mode="json") for item in service.list_history(version_id)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/dataset-versions/{version_id}/field-candidates", status_code=201)
def create_dataset_card_candidate(
    version_id: UUID,
    request: CandidateCreateRequest,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.add_candidate(version_id, request).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/dataset-versions/{version_id}/field-candidates")
def list_dataset_card_candidates(
    version_id: UUID,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return [
            item.model_dump(mode="json") for item in service.list_candidates(version_id)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/dataset-versions/{version_id}/field-candidates/{candidate_id}:review"
)
def review_dataset_card_candidate(
    version_id: UUID,
    candidate_id: UUID,
    request: CandidateReviewRequest,
    service: DatasetCardService = Depends(get_dataset_card_service),
):
    try:
        return service.review_candidate(
            version_id, candidate_id, request
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dataset-versions/{version_id}/profile-snapshots", status_code=201)
def create_profile_snapshot(
    version_id: UUID,
    request: ProfileSnapshotCreate,
    service: ProfileSnapshotService = Depends(get_profile_snapshot_service),
):
    try:
        snapshot, reference = service.create_or_reuse(
            version_id,
            rule_set_refs=request.rule_set_refs,
            calculator_bindings=request.calculator_bindings,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "snapshot": snapshot.model_dump(mode="json"),
        "reference": reference.model_dump(mode="json"),
    }


@router.get("/profile-snapshots/{snapshot_id}")
def get_profile_snapshot(
    snapshot_id: UUID,
    service: ProfileSnapshotService = Depends(get_profile_snapshot_service),
):
    try:
        snapshot = service.get(snapshot_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="ProfileSnapshot not found")
    return snapshot.model_dump(mode="json")

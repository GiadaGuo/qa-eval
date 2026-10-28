# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 16:28:23
# @LastEditTime: 2026-10-22 02:26:50
# @Description:
# FastAPI 依赖注入
# 1. PostgreSQL：平台元数据事务管理与业务服务
# 2. MySQL：外部数据源只读访问
# ====================================================

import os
from collections.abc import Generator

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from qa_eval.application.agent.service import AgentSessionService
from qa_eval.application.services.dataset_service import (
    DatasetService,
)
from qa_eval.application.services.dataset_version_service import (
    DatasetVersionService,
)
from qa_eval.application.services.document_report_task_service import (
    DocumentReportTaskService,
)
from qa_eval.application.services.document_version_service import (
    DocumentVersionService,
)
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import QualityContractService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.config import get_settings
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_profile import (
    SQLAlchemyDatasetProfileRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset_version import (
    SQLAlchemyDatasetVersionRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_document_version import (
    SQLAlchemyDocumentVersionRepository,
)
from qa_eval.infrastructure.db.session import SessionLocal
from qa_eval.infrastructure.llm.client import OpenAICompatibleLLMClient
from qa_eval.infrastructure.mysql.datasource_reader import (
    MySQLDataSourceReader,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)
from qa_eval.infrastructure.storage.s3_artifact_store import S3ArtifactStore

load_dotenv()


# ====================================================
# 1. PostgreSQL 平台元数据库依赖
# ====================================================
def get_db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_dataset_service(
    session: Session = Depends(get_db_session),
) -> DatasetService:
    repository = SQLAlchemyDatasetRepository(session)
    version_repository = SQLAlchemyDatasetVersionRepository(session)
    document_version_repository = SQLAlchemyDocumentVersionRepository(session)
    return DatasetService(repository, version_repository, document_version_repository)


def get_metric_registry_service(
    session: Session = Depends(get_db_session),
) -> MetricRegistryService:
    return MetricRegistryService(session)


def get_quality_contract_service(
    session: Session = Depends(get_db_session),
) -> QualityContractService:
    return QualityContractService(session)


def get_evaluation_proposal_service(
    session: Session = Depends(get_db_session),
) -> EvaluationProposalService:
    return EvaluationProposalService(session)


# ====================================================
# 2. Artifact Store 依赖
# ====================================================


def get_artifact_store() -> ArtifactStore:
    """创建 ArtifactStore 实例。"""
    settings = get_settings()

    if settings.artifact_store_type == "s3":
        if not settings.artifact_store_s3_bucket:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="S3 artifact store bucket is not configured",
            )
        return S3ArtifactStore(
            bucket=settings.artifact_store_s3_bucket,
            prefix=settings.artifact_store_s3_prefix,
            endpoint_url=settings.artifact_store_s3_endpoint_url,
            region_name=settings.artifact_store_s3_region_name,
            access_key_id=settings.artifact_store_s3_access_key_id,
            secret_access_key=settings.artifact_store_s3_secret_access_key,
        )

    return LocalFileSystemArtifactStore(settings.artifact_store_local_root)


def get_agent_session_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> AgentSessionService:
    return AgentSessionService(session, artifact_store=artifact_store)


def get_evaluation_run_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> EvaluationRunService:
    return EvaluationRunService(session, artifact_store)


def get_quality_result_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> QualityResultService:
    return QualityResultService(session, artifact_store)


def get_dataset_version_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> DatasetVersionService:
    return DatasetVersionService(
        dataset_repository=SQLAlchemyDatasetRepository(session),
        version_repository=SQLAlchemyDatasetVersionRepository(session),
        profile_repository=SQLAlchemyDatasetProfileRepository(session),
        artifact_store=artifact_store,
    )


def get_document_version_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> DocumentVersionService:
    return DocumentVersionService(
        dataset_repository=SQLAlchemyDatasetRepository(session),
        document_version_repository=SQLAlchemyDocumentVersionRepository(session),
        artifact_store=artifact_store,
    )


# ====================================================
# 3. MySQL 外部数据源依赖
# ====================================================


def get_mysql_reader() -> MySQLDataSourceReader:
    """创建 MySQL 数据源 Reader。"""

    required_keys = (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_TABLE",
    )

    missing = [key for key in required_keys if not os.getenv(key)]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MySQL datasource is not configured",
        )

    try:
        return MySQLDataSourceReader(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ["MYSQL_PORT"]),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"],
            table=os.environ["MYSQL_TABLE"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid MySQL datasource configuration",
        ) from exc


# ====================================================
# 4. 说明文档评测（LLM 网关 + 报告任务）依赖
# ====================================================


def get_document_llm_client() -> OpenAICompatibleLLMClient:
    """LLM 网关未配置（base_url / api_key / model 任一缺失）即 503。"""

    client = OpenAICompatibleLLMClient.from_settings(get_settings())
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document evaluation LLM gateway is not configured",
        )
    return client


def get_document_report_task_service(
    session: Session = Depends(get_db_session),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> DocumentReportTaskService:
    # 查询路由不需要 LLM 客户端；执行入口在后台线程内自行构建
    return DocumentReportTaskService(
        session, artifact_store, get_settings(), llm_client=None
    )

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# P1 验收：DocumentVersion 上传→列表→下载哈希一致→删除；
# 被报告任务引用的版本删除返回 409 语义（DocumentVersionInUseError）。
# ====================================================

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from qa_eval.application.services.document_version_service import (
    DocumentVersionService,
)
from qa_eval.domain.exceptions import DocumentVersionInUseError
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.document_version import (
    DocumentReportTaskModel,
    DocumentVersionModel,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_dataset import (
    SQLAlchemyDatasetRepository,
)
from qa_eval.infrastructure.db.repositories.sqlalchemy_document_version import (
    SQLAlchemyDocumentVersionRepository,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

CONTENT_V1 = "# 数据集说明\n\n本数据集包含 1,000 条记录。\n".encode()
CONTENT_V2 = CONTENT_V1 + "\n## 更新\n\n新增访问渠道说明。\n".encode()


def _service(db_session, tmp_path) -> DocumentVersionService:
    return DocumentVersionService(
        dataset_repository=SQLAlchemyDatasetRepository(db_session),
        document_version_repository=SQLAlchemyDocumentVersionRepository(db_session),
        artifact_store=LocalFileSystemArtifactStore(tmp_path),
    )


def _dataset(db_session) -> DatasetModel:
    dataset = DatasetModel(
        id=uuid4(), name=f"docs-{uuid4().hex[:8]}", modality=["text"]
    )
    db_session.add(dataset)
    db_session.flush()
    return dataset


def _report_task(document_version_id) -> DocumentReportTaskModel:
    return DocumentReportTaskModel(
        id=uuid4(),
        document_version_id=document_version_id,
        status="SUCCEEDED",
        evaluator_version="0.1.0",
        standard_version="1.0.0",
        standard_sha256="0" * 64,
        config={},
        document={},
    )


def test_upload_list_download_delete_flow(db_session, tmp_path):
    service = _service(db_session, tmp_path)
    dataset = _dataset(db_session)

    first = service.upload_document(dataset.id, "readme.md", CONTENT_V1)
    second = service.upload_document(dataset.id, "readme-v2.md", CONTENT_V2)

    assert first.version == "v1"
    assert second.version == "v2"
    assert first.sha256 == hashlib.sha256(CONTENT_V1).hexdigest()

    documents = service.list_documents(dataset.id)
    assert {d.version for d in documents} == {"v1", "v2"}

    # 下载内容哈希与上传记录一致
    _, downloaded = service.download_document(first.id)
    assert hashlib.sha256(downloaded).hexdigest() == first.sha256
    assert downloaded == CONTENT_V1

    metadata = service.get_document_metadata(first.id)
    assert metadata["line_count"] == 3
    assert metadata["parse_status"] == "NOT_PARSED"

    assert service.delete_document(first.id) is True
    assert service.get_document(first.id) is None
    remaining = service.list_documents(dataset.id)
    assert [d.version for d in remaining] == ["v2"]

    # 删除后其余版本可继续下载
    _, still_downloadable = service.download_document(second.id)
    assert still_downloadable == CONTENT_V2


def test_delete_blocked_by_report_task_reference(db_session, tmp_path):
    service = _service(db_session, tmp_path)
    dataset = _dataset(db_session)
    version = service.upload_document(dataset.id, "readme.md", CONTENT_V1)
    db_session.add(_report_task(version.id))
    db_session.flush()

    with pytest.raises(DocumentVersionInUseError) as exc_info:
        service.delete_document(version.id)

    assert exc_info.value.code == "DOCUMENT_VERSION_IN_USE"
    assert exc_info.value.dependencies == {"document_report_tasks": 1}
    assert service.get_document(version.id) is not None


def test_upload_rejects_invalid_documents_before_persisting(
    db_session,
    tmp_path,
):
    service = _service(db_session, tmp_path)
    dataset = _dataset(db_session)

    for filename, content in [
        ("readme.pdf", CONTENT_V1),  # 非 .md
        ("readme.md", b"a" * (2 * 1024 * 1024 + 1)),  # 超限
        ("readme.md", b"\xff\xfe\x00invalid"),  # 非 UTF-8
        ("readme.md", b""),  # 空文件
    ]:
        with pytest.raises(ValueError):
            service.upload_document(dataset.id, filename, content)

    assert service.list_documents(dataset.id) == []


def test_unique_constraint_on_dataset_and_version(db_session):
    dataset = _dataset(db_session)

    def _row(version: str) -> DocumentVersionModel:
        return DocumentVersionModel(
            id=uuid4(),
            dataset_id=dataset.id,
            version=version,
            sha256="0" * 64,
            size_bytes=len(CONTENT_V1),
            storage_uri=f"datasets/{dataset.id}/documents/{uuid4()}/readme.md",
            document={"filename": "readme.md", "format": "markdown"},
        )

    db_session.add(_row("v1"))
    db_session.flush()
    db_session.add(_row("v1"))

    with pytest.raises(IntegrityError):
        db_session.flush()

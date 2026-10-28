# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# P1：说明文档版本 API：multipart 上传、列表、元数据、下载、删除。
# ====================================================

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from apps.api.dependencies import get_document_version_service
from qa_eval.application.services.document_version_service import (
    DocumentVersionService,
)
from qa_eval.domain.entities.document_version import DocumentVersion
from qa_eval.domain.exceptions import DocumentVersionInUseError

router = APIRouter(prefix="/api/v1", tags=["document-versions"])


class DocumentVersionResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    version: str
    filename: str
    format: str
    size_bytes: int
    sha256: str
    created_at: str | None


def _document_response(version: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        filename=version.filename,
        format=version.format,
        size_bytes=version.size_bytes,
        sha256=version.sha256,
        created_at=version.created_at.isoformat() if version.created_at else None,
    )


@router.post(
    "/datasets/{dataset_id}/document-versions",
    response_model=DocumentVersionResponse,
    status_code=201,
)
def upload_document_version(
    dataset_id: UUID,
    file: Annotated[UploadFile, File()],
    service: DocumentVersionService = Depends(get_document_version_service),
):
    if file.filename is None or not file.filename.strip().endswith(".md"):
        raise HTTPException(
            status_code=422,
            detail="仅支持 Markdown（.md）文件；PDF/HTML/DOCX 等暂不支持",
        )

    content = file.file.read()
    try:
        version = service.upload_document(dataset_id, file.filename, content)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _document_response(version)


@router.get(
    "/datasets/{dataset_id}/document-versions",
    response_model=list[DocumentVersionResponse],
)
def list_document_versions(
    dataset_id: UUID,
    service: DocumentVersionService = Depends(get_document_version_service),
):
    return [
        _document_response(version) for version in service.list_documents(dataset_id)
    ]


@router.get("/document-versions/{document_version_id}")
def get_document_version_metadata(
    document_version_id: UUID,
    service: DocumentVersionService = Depends(get_document_version_service),
):
    metadata = service.get_document_metadata(document_version_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="DocumentVersion not found")
    return metadata


@router.get("/document-versions/{document_version_id}/download")
def download_document_version(
    document_version_id: UUID,
    service: DocumentVersionService = Depends(get_document_version_service),
):
    try:
        version, content = service.download_document(document_version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    quoted = quote(version.filename)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
            )
        },
    )


@router.delete("/document-versions/{document_version_id}", status_code=204)
def delete_document_version(
    document_version_id: UUID,
    service: DocumentVersionService = Depends(get_document_version_service),
):
    try:
        deleted = service.delete_document(document_version_id)
    except DocumentVersionInUseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "dependencies": exc.dependencies,
            },
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="DocumentVersion not found",
        )

# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 17:02:40
# @LastEditTime: 2026-10-19 17:02:59
# @Description: 使用 Pydantic，因为 Manifest 本身就是需要序列化、校验和跨模块流转的数据契约
# ====================================================

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ManifestFile(BaseModel):
    file_id: str
    uri: str
    format: str
    split: str | None = None
    size_bytes: int = Field(ge=0)
    checksum_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class SchemaMapping(BaseModel):
    adapter_id: str
    adapter_version: str
    field_mapping: dict[str, str]


class DatasetManifest(BaseModel):
    dataset_id: UUID
    dataset_version_id: UUID
    dataset_version: str
    name: str
    schema_version: str = "1.0"

    files: list[ManifestFile] = Field(default_factory=list)
    modalities: list[str] = Field(min_length=1)

    schema_mapping: SchemaMapping

    application_context: dict[str, Any] = Field(
        default_factory=dict
    )
    governance: dict[str, Any] = Field(
        default_factory=dict
    )
    registration: dict[str, Any] = Field(
        default_factory=dict
    )

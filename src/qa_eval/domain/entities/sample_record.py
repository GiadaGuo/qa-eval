# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 09:38:31
# @LastEditTime: 2026-10-21 09:38:44
# @Description: 
# ====================================================

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MediaType = Literal[
    "text",
    "image",
    "audio",
    "video",
    "structured",
]


class ContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    media_type: MediaType
    role: str = Field(min_length=1)

    # 文本可直接保存，非文本通常使用对象存储 URI
    content: str | None = None
    uri: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.content is None and self.uri is None:
            raise ValueError(
                "ContentItem requires content or uri"
            )
        return self


class SampleSource(BaseModel):
    source_type: str = Field(min_length=1)
    source_uri: str | None = None
    source_record_id: str | None = None
    source_row: int | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SampleLineage(BaseModel):
    adapter_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    input_fingerprint: str | None = None



class SampleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "1.1"] = "1.1"
    dataset_version_id: UUID
    sample_id: str = Field(min_length=1)

    rid: list[str] = Field(default_factory=list)

    # 核心数据：保留多内容项结构
    data_content: list[ContentItem] = Field(min_length=1)

    # 用户选择的元数据字段
    annotations: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # 用户选择的时间字段
    timestamps: dict[str, str | None] = Field(
        default_factory=dict
    )

    # 未归入其他分组的原始字段
    other_fields: dict[str, Any] = Field(
        default_factory=dict
    )

    source: SampleSource
    lineage: SampleLineage

    @model_validator(mode="after")
    def validate_content_ids(self):
        ids = [
            item.content_id
            for item in self.data_content
        ]

        if len(ids) != len(set(ids)):
            raise ValueError(
                "content_id must be unique within a sample"
            )

        return self

    def get_content_by_role(
        self,
        role: str,
    ) -> list[ContentItem]:
        return [
            item
            for item in self.data_content
            if item.role == role
        ]

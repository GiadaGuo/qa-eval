from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WireModel(BaseModel):
    """Strict base class for canonical JSON wire contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TimestampedWireModel(WireModel):
    @field_validator("created_at", check_fields=False)
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("platform timestamps must include a timezone")
        return value


JsonObject = dict[str, Any]


class VersionTarget(WireModel):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": [field],
                    "properties": {field: {"type": "string"}, other: {"type": "null"}},
                }
                for field, other in (
                    ("dataset_version_id", "document_version_id"),
                    ("document_version_id", "dataset_version_id"),
                )
            ]
        }
    )

    dataset_version_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    document_version_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def one_version_target(self):
        if (self.dataset_version_id is None) == (self.document_version_id is None):
            raise ValueError("exactly one evaluation version target is required")
        return self

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


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

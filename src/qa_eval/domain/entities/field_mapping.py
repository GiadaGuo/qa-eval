
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceField(BaseModel):
    """MySQL Schema Discovery 返回的字段描述。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullable: bool = True
    is_primary_key: bool = False


class FieldMappingConfig(BaseModel):
    """用户确认的字段分类方案。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"

    sample_id_field: str = Field(min_length=1)

    core_fields: list[str] = Field(min_length=1)
    metadata_fields: list[str] = Field(default_factory=list)
    time_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_no_duplicates(self):
        groups = {
            "identity": [self.sample_id_field],
            "core": self.core_fields,
            "metadata": self.metadata_fields,
            "time": self.time_fields,
        }

        seen = {}

        for group_name, fields in groups.items():
            for field_name in fields:
                if not field_name.strip():
                    raise ValueError(
                        f"Empty field name in {group_name}"
                    )

                if field_name in seen:
                    raise ValueError(
                        f"Field '{field_name}' assigned more than once: "
                        f"{seen[field_name]} and {group_name}"
                    )

                seen[field_name] = group_name

        return self

    def resolve(
        self,
        source_fields: list[SourceField],
    ) -> "ResolvedFieldMapping":
        """根据真实表结构计算完整字段分组。"""

        all_names = [field.name for field in source_fields]

        if len(all_names) != len(set(all_names)):
            raise ValueError(
                "Duplicate field names in source schema"
            )

        all_fields = set(all_names)

        assigned = {
            self.sample_id_field,
            *self.core_fields,
            *self.metadata_fields,
            *self.time_fields,
        }

        unknown = assigned - all_fields

        if unknown:
            raise ValueError(
                f"Unknown source fields: {sorted(unknown)}"
            )

        # 保持 MySQL 原始列顺序
        other_fields = [
            name
            for name in all_names
            if name not in assigned
        ]

        return ResolvedFieldMapping(
            sample_id_field=self.sample_id_field,
            core_fields=self.core_fields.copy(),
            metadata_fields=self.metadata_fields.copy(),
            time_fields=self.time_fields.copy(),
            other_fields=other_fields,
        )


class ResolvedFieldMapping(BaseModel):
    """字段分类完成后的完整结果。"""

    model_config = ConfigDict(extra="forbid")

    sample_id_field: str
    core_fields: list[str]
    metadata_fields: list[str]
    time_fields: list[str]
    other_fields: list[str]

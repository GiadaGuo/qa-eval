# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 11:36:45
# @LastEditTime: 2026-10-21 11:46:20
# @Description: 按照用户确认的分组，把字段值放到对应位置
# ====================================================

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from qa_eval.domain.entities.field_mapping import ResolvedFieldMapping
from qa_eval.domain.entities.sample_record import (
    ContentItem,
    SampleLineage,
    SampleRecord,
    SampleSource,
)
from qa_eval.domain.ports.dataset_adapter import AdapterContext


class MySQLRecordAdapter:
    """按照用户确认的字段分类转换 MySQL 原始记录。"""

    adapter_id = "mysql_record"
    adapter_version = "1.0.0"

    def __init__(self, mapping: ResolvedFieldMapping):
        self.mapping = mapping

    @staticmethod
    def _json_value(value: Any) -> Any:
        """将常见 MySQL Python 值转换为 JSON 兼容类型。"""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, (datetime, date)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return str(value)

        if isinstance(value, UUID):
            return str(value)

        if isinstance(value, bytes):
            raise TypeError(
                "Binary fields are not supported in P1-1.2.4"
            )

        raise ValueError(
            f"Unsupported MySQL value type: {type(value).__name__}"
        )

    @staticmethod
    def _text_content(value: Any) -> str:
        """核心数据字段当前按文本内容处理。"""
        if value is None:
            return ""

        if not isinstance(value, str):
            raise TypeError(
                "Core data field must be text"
            )

        return value

    @staticmethod
    def _timestamp_value(value: Any) -> str | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                # MySQL DATETIME 本身通常没有时区
                # 不擅自假设为 UTC
                return value.isoformat()
            return value.isoformat()

        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, str):
            try:
                datetime.fromisoformat(
                    value
                )
            except ValueError as exc:
                raise ValueError(
                    f"Invalid timestamp: {value}"
                ) from exc
            return value

        raise ValueError(
            f"Unsupported timestamp type: {type(value).__name__}"
        )

    def adapt(
        self,
        raw_record: Mapping[str, Any],
        context: AdapterContext,
    ) -> SampleRecord:
        expected_fields = [
            self.mapping.sample_id_field,
            *self.mapping.core_fields,
            *self.mapping.metadata_fields,
            *self.mapping.time_fields,
            *self.mapping.other_fields,
        ]

        missing_fields = [
            field
            for field in expected_fields
            if field not in raw_record
        ]

        if missing_fields:
            raise ValueError(
                f"Missing source fields: {missing_fields}"
            )

        raw_id = raw_record[self.mapping.sample_id_field]

        if raw_id is None or not str(raw_id).strip():
            raise ValueError("Sample ID cannot be empty")

        sample_id = str(raw_id)

        data_content = [
            ContentItem(
                content_id=field,
                role=field,
                media_type="text",
                content=self._text_content(raw_record[field]),
            )
            for field in self.mapping.core_fields
        ]

        metadata = {
            field: self._json_value(raw_record[field])
            for field in self.mapping.metadata_fields
        }

        timestamps = {
            field: self._timestamp_value(raw_record[field])
            for field in self.mapping.time_fields
        }

        other_fields = {
            field: self._json_value(raw_record[field])
            for field in self.mapping.other_fields
        }

        return SampleRecord(
            schema_version="1.1",
            dataset_version_id=context.dataset_version_id,
            sample_id=sample_id,
            rid=[sample_id],
            data_content=data_content,
            metadata=metadata,
            timestamps=timestamps,
            other_fields=other_fields,
            source=SampleSource(
                source_type="mysql",
                source_uri=context.source_uri,
                source_record_id=sample_id,
            ),
            lineage=SampleLineage(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                input_fingerprint=context.input_fingerprint,
            ),
        )

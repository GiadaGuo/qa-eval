# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-21 10:12:47
# @LastEditTime: 2026-10-21 11:14:31
# @Description: 
# ====================================================

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from qa_eval.domain.entities.sample_record import (
    ContentItem,
    SampleLineage,
    SampleRecord,
    SampleSource,
)
from qa_eval.domain.ports.dataset_adapter import (
    AdapterContext,
    MappingConfig,
)


class GenericMappingAdapter:
    def __init__(self, config: MappingConfig):
        self.config = config
        self.adapter_id = config.adapter_id
        self.adapter_version = config.adapter_version

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    @staticmethod
    def _text_value(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError(
                "Text content must be a string"
            )
        return value

    def adapt(
        self,
        raw_record: Mapping[str, Any],
        context: AdapterContext,
    ) -> SampleRecord:
        id_field = self.config.sample_id_field

        if id_field not in raw_record:
            raise ValueError(
                f"Missing sample ID field: {id_field}"
            )

        raw_id = raw_record[id_field]

        if raw_id is None or not str(raw_id).strip():
            raise ValueError(
                f"Empty sample ID field: {id_field}"
            )

        sample_id = str(raw_id)

        content_items = []

        for item in self.config.content_mapping:
            if item.source_field not in raw_record:
                raise ValueError(
                    "Missing content field: "
                    f"{item.source_field}"
                )

            value = raw_record[item.source_field]

            if item.media_type != "text":
                raise ValueError(
                    "P1-1.2 supports text mapping only"
                )

            content_items.append(
                ContentItem(
                    content_id=item.role,
                    media_type=item.media_type,
                    role=item.role,
                    content=self._text_value(value),
                )
            )

        metadata = {
            target: self._serialize_value(
                raw_record.get(source)
            )
            for target, source
            in self.config.metadata_mapping.items()
        }

        source_attributes = {
            target: self._serialize_value(
                raw_record.get(source)
            )
            for target, source
            in self.config.source_mapping.items()
        }

        return SampleRecord(
            dataset_version_id=context.dataset_version_id,
            sample_id=sample_id,
            rid=[sample_id],
            data_content=content_items,
            metadata=metadata,
            source=SampleSource(
                source_type="mysql", # 本次 MVP 的明确范围。未来支持 JSONL、Parquet 时，再将来源类型放入上下文或读取层，不需要现在提前设计完整的多数据源插件系统
                source_uri=context.source_uri,
                source_record_id=sample_id,
                attributes=source_attributes,
            ),
            lineage=SampleLineage(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                input_fingerprint=context.input_fingerprint,
            ),
        )

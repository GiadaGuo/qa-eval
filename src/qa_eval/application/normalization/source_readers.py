from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class SourceDescription:
    format: str
    media_type: str
    source_type: str
    record_count: int
    fields: list[dict[str, Any]]
    schema_fingerprint_input: str


class TabularSourceReader(Protocol):
    def describe(self) -> SourceDescription: ...

    def iter_rows(self) -> Iterator[tuple[int, Mapping[str, Any]]]: ...

    def preview(self, limit: int) -> list[dict[str, Any]]: ...


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    raise TypeError(f"Unsupported source value type: {type(value).__name__}")


class JsonlSourceReader:
    def __init__(self, path: Path):
        self.path = path

    def iter_rows(self) -> Iterator[tuple[int, Mapping[str, Any]]]:
        try:
            source = self.path.open(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("JSONL file must be UTF-8 encoded") from exc
        with source:
            source_row = 0
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON on line {line_number}: {exc.msg}"
                    ) from exc
                if not isinstance(record, dict):
                    raise TypeError(f"Line {line_number} must be a JSON object")
                yield source_row, record
                source_row += 1

    def describe(self) -> SourceDescription:
        field_order: list[str] = []
        field_types: dict[str, set[str]] = {}
        present: dict[str, int] = {}
        nulls: dict[str, int] = {}
        count = 0
        for _, record in self.iter_rows():
            count += 1
            for name, value in record.items():
                if name not in field_types:
                    field_order.append(name)
                    field_types[name] = set()
                    present[name] = 0
                    nulls[name] = 0
                present[name] += 1
                field_types[name].add(_json_type(value))
                nulls[name] += value is None
        if count == 0:
            raise ValueError("JSONL file contains no records")
        fields = [
            {
                "name": name,
                "data_type": "|".join(sorted(field_types[name])),
                "nullable": present[name] < count or nulls[name] > 0,
                "is_primary_key": False,
            }
            for name in field_order
        ]
        schema = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return SourceDescription(
            format="jsonl",
            media_type="application/x-ndjson",
            source_type="jsonl_upload",
            record_count=count,
            fields=fields,
            schema_fingerprint_input=schema,
        )

    def preview(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, record in self.iter_rows():
            rows.append(dict(record))
            if len(rows) >= limit:
                break
        return rows


class ParquetSourceReader:
    def __init__(self, path: Path, *, batch_size: int = 2048):
        self.path = path
        self.batch_size = batch_size
        try:
            self._file = pq.ParquetFile(path)
        except (OSError, pa.ArrowException) as exc:
            raise ValueError(f"Invalid Parquet file: {exc}") from exc
        if self._file.metadata.num_rows == 0:
            raise ValueError("Parquet file contains no records")

    def describe(self) -> SourceDescription:
        arrow_schema = self._file.schema_arrow
        fields = [
            {
                "name": field.name,
                "data_type": str(field.type),
                "nullable": field.nullable,
                "is_primary_key": False,
            }
            for field in arrow_schema
        ]
        return SourceDescription(
            format="parquet",
            media_type="application/vnd.apache.parquet",
            source_type="parquet_upload",
            record_count=self._file.metadata.num_rows,
            fields=fields,
            schema_fingerprint_input=str(arrow_schema),
        )

    def iter_rows(self) -> Iterator[tuple[int, Mapping[str, Any]]]:
        source_row = 0
        for batch in self._file.iter_batches(batch_size=self.batch_size):
            for record in batch.to_pylist():
                yield (
                    source_row,
                    {name: json_compatible(value) for name, value in record.items()},
                )
                source_row += 1

    def preview(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, record in self.iter_rows():
            rows.append(dict(record))
            if len(rows) >= limit:
                break
        return rows


def source_reader(path: Path, format_name: str) -> TabularSourceReader:
    if format_name == "jsonl":
        return JsonlSourceReader(path)
    if format_name == "parquet":
        return ParquetSourceReader(path)
    raise ValueError(f"Unsupported source format: {format_name}")

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from qa_eval.application.normalization.jhsjk_article_normalizer import (
    JhsjkArticleJsonlNormalizer,
)
from qa_eval.application.normalization.source_readers import ParquetSourceReader
from qa_eval.contracts.datasets import FieldMappingSpecV2
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _write_parquet(path: Path) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "iid": "1",
                "title": "标题",
                "content": "<p>第一段</p><p>第二段</p>",
                "pubtime": 1790814600000,
                "organization": "机构甲",
                "publishunit": "机构乙",
                "level": "市级",
                "attachname": "附件.pdf",
                "attachurl": "https://example.test/a.pdf",
            }
        ]
    )
    pq.write_table(table, path, row_group_size=1)


def _mapping() -> FieldMappingSpecV2:
    def rule(source, kind, name=None, role=None, value_type="string"):
        target = {"kind": kind}
        if name:
            target["name"] = name
        if role:
            target.update(role=role, modality="text")
        return {
            "source_path": f"$.{source}",
            "action": "MAP",
            "target": target,
            "value_type": value_type,
            "presence_policy": "PRESERVE",
            "required_for_normalization": False,
        }

    mappings = [
        rule("iid", "SAMPLE_ID", "sample_id"),
        rule("title", "CONTENT", "title", "article.title"),
        rule("content", "CONTENT", "body-html", "article.body.html"),
        rule("content", "CONTENT", "body-plain", "article.body.plain"),
        rule("pubtime", "TEMPORAL", "published_at", value_type="epoch_milliseconds"),
        rule("organization", "METADATA", "publisher.organization"),
        rule("publishunit", "METADATA", "publisher.publishunit"),
        rule("level", "METADATA", "policy_level.level"),
        rule("attachname", "METADATA", "attachments.name"),
        rule("attachurl", "METADATA", "attachments.url"),
    ]
    mappings[0]["presence_policy"] = "REJECT_MISSING"
    mappings[0]["required_for_normalization"] = True
    return FieldMappingSpecV2.model_validate(
        {
            "schema_version": "2.0",
            "adapter": {"key": "jhsjk_policy_article", "version": "2.0.0"},
            "mappings": mappings,
        }
    )


def test_parquet_reader_describes_and_iterates_batches(tmp_path: Path) -> None:
    path = tmp_path / "source.parquet"
    _write_parquet(path)

    reader = ParquetSourceReader(path, batch_size=1)
    description = reader.describe()
    rows = list(reader.iter_rows())

    assert description.format == "parquet"
    assert description.media_type == "application/vnd.apache.parquet"
    assert description.record_count == 1
    assert rows[0][1]["iid"] == "1"


def test_parquet_policy_normalization_produces_canonical_sample(tmp_path: Path) -> None:
    path = tmp_path / "source.parquet"
    _write_parquet(path)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    result = JhsjkArticleJsonlNormalizer(store).normalize(
        path,
        dataset_id=uuid4(),
        dataset_version_id=uuid4(),
        field_mapping=_mapping(),
        input_format="parquet",
    )

    assert result.normalized_count == 1
    assert result.rejected_count == 0
    assert result.raw_artifact.format == "parquet"
    sample = json.loads(store.read(result.normalized_artifact.uri))
    contents = {item["role"]: item for item in sample["contents"]}
    assert contents["article.body.html"]["payload"]["format"] == "html"
    assert contents["article.body.plain"]["payload"]["text"] == "第一段\n第二段"
    assert sample["metadata"]["temporal"]["published_at"]["value"].endswith("+00:00")
    assert sample["metadata"]["publisher"]["value"] == "机构甲"
    assert sample["metadata"]["policy_level"]["value"] == "MUNICIPAL"
    assert sample["metadata"]["attachments"] == [
        {"name": "附件.pdf", "url": "https://example.test/a.pdf"}
    ]
    warnings = sample["metadata"]["normalization"]["warnings"]
    assert warnings[0]["code"] == "PUBLISHER_INCONSISTENT"
    assert result.manifest.source_snapshot.details["upload_format"] == "parquet"


def test_invalid_parquet_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"not parquet")

    try:
        ParquetSourceReader(path)
    except ValueError as exc:
        assert "Invalid Parquet file" in str(exc)
    else:
        raise AssertionError("invalid Parquet should fail")

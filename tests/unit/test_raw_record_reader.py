import hashlib
from decimal import Decimal
from uuid import uuid4

import pytest

from qa_eval.application.normalization.record_reader import (
    RawJsonlRecordReader,
    RawRecordManifest,
    read_records,
)


def manifest_for(content):
    return RawRecordManifest(
        dataset_version_id=uuid4(),
        source_uri="datasets/example/raw.jsonl",
        source_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        schema_fingerprint="sha256:" + "0" * 64,
    )


@pytest.mark.parametrize(
    "source",
    [
        b'{"iid":"1","title":"","content":"<p>body</p>","targetContent":null}',
        b'{"id":"1","content":"<p>body</p>","pubtime":1700000000000,"indexid":null}',
        b'{"id":"1","content":"<p>body</p>","plain_text_content":"body"}',
    ],
)
def test_policy_record_views_keep_source_fields_and_byte_locations(tmp_path, source):
    content = source + b'\r\n\n{"unknown.key":1.0000000000000000001,"x":null}\n'
    path = tmp_path / "raw.jsonl"
    path.write_bytes(content)
    manifest = manifest_for(content)
    first, second = read_records(path, manifest=manifest)
    assert first.data["content"] == "<p>body</p>"
    assert first.field("missing") == ("MISSING", None)
    assert second.field("x") == ("NULL", None)
    assert second.field("unknown.key") == ("PRESENT", Decimal("1.0000000000000000001"))
    if "title" in first.data:
        assert first.field("title") == ("EMPTY", "")
    assert second.meta.line_number == 3
    start = second.meta.byte_start
    end = start + second.meta.byte_length
    assert RawJsonlRecordReader(manifest).decode(
        content[start:end], start=start, number=3
    ) == second


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"outer":{"a":1,"a":2}}', "duplicate JSON"),
        (b'{"a":NaN}', "non-finite"),
        (b"[]", "must be an object"),
        (b"{}", "checksum"),
    ],
)
def test_invalid_records_and_mismatched_artifact_are_rejected(tmp_path, content, message):
    path = tmp_path / "raw.jsonl"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        list(read_records(path, manifest=manifest_for(b'{"expected":true}')))


def test_masked_policy_source_schemas_are_read_without_field_projection():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "dataflow"
    for name, field_count in (
        ("policy.policy", 39),
        ("policy.policy_documents", 17),
        ("policy.jhsjk_article", 10),
    ):
        import json

        path = root / f"{name}.jsonl"
        raw = path.read_bytes()
        record, = read_records(path, manifest=manifest_for(raw))
        assert len(record.data) == field_count
        assert record.data == json.loads(raw)


def test_raw_evidence_uses_original_unicode_coordinates(tmp_path):
    from qa_eval.application.evidence.raw_location import RawTextLocation

    text = "<p>甲🙂乙</p>"
    content = ('{"content":"' + text + '"}').encode()
    path = tmp_path / "raw.jsonl"
    path.write_bytes(content)
    record, = read_records(path, manifest=manifest_for(content))
    location = RawTextLocation(
        field_tokens=("content",),
        artifact_sha256=record.meta.artifact_sha256,
        text_sha256="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        start=4,
        end=5,
    )
    assert location.excerpt(record) == "🙂"
    with pytest.raises(ValueError, match="text checksum"):
        location.model_copy(update={"text_sha256": "wrong"}).excerpt(record)

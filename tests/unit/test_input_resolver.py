import hashlib
import io
from uuid import uuid4

import pytest

from qa_eval.application.normalization.input_resolver import InputResolver, Transform
from qa_eval.application.normalization.record_reader import RawJsonlRecordReader, RawRecordManifest
from qa_eval.contracts.logical_inputs import (
    CalculatorInputContract,
    FieldBinding,
    InputSlot,
    MappingRevision,
)


def test_frozen_mapping_and_multi_field_diagnostics():
    content = b'{"title":null,"content":"<p>A</p>"}\n{"title":"","content":"invalid"}\n{}\n'
    manifest = RawRecordManifest(
        dataset_version_id=uuid4(),
        source_uri="raw.jsonl",
        source_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        schema_fingerprint="sha256:" + "0" * 64,
    )

    def transform(value):
        if not value.startswith("<p>"):
            raise ValueError("unsupported")
        return value[3:-4]

    resolver = InputResolver([
        Transform("html_to_plain@fixture", "digest-1", "text/html", "text/plain", transform),
    ])
    mapping = MappingRevision(
        dataset_version_id=manifest.dataset_version_id,
        schema_fingerprint=manifest.schema_fingerprint,
        bindings=(
            FieldBinding(semantic="title", field_tokens=("title",), representation="text/plain"),
            FieldBinding(
                semantic="body", field_tokens=("content",), representation="text/plain",
                transform="html_to_plain@fixture", source_representation="text/html",
            ),
        ),
    )
    contract = CalculatorInputContract(
        version="1", grain="RECORD", mode="DIRECT",
        slots=(
            InputSlot(name="title", semantic="title", representation="text/plain", value_type="string"),
            InputSlot(name="body", semantic="body", representation="text/plain", value_type="string"),
        ),
    )

    def records():
        return RawJsonlRecordReader(manifest).iter_records(io.BytesIO(content))

    plan = resolver.preflight(manifest, mapping, contract)
    changed = mapping.model_copy(update={"bindings": tuple(reversed(mapping.bindings))})
    assert resolver.preflight(manifest, changed, contract).fingerprint() != plan.fingerprint()
    first, second, third = resolver.iter_records(plan, records())
    assert first.values["title"].status == "NULL"
    assert first.values["body"].value == "A"
    assert first.values["body"].origin.field_tokens == ("content",)
    assert second.values["title"].status == "EMPTY"
    assert second.values["body"].status == "TRANSFORM_FAILED"
    assert third.values["title"].status == "MISSING"
    assert list(resolver.iter_records(plan, records())) == [first, second, third]
    with pytest.raises(ValueError, match="frozen transform"):
        list(InputResolver().iter_records(plan, records()))
    with pytest.raises(ValueError, match="unbound required slot"):
        resolver.preflight(manifest, mapping.model_copy(update={"bindings": ()}), contract)
    with pytest.raises(ValueError, match="ambiguous"):
        MappingRevision(
            dataset_version_id=manifest.dataset_version_id,
            schema_fingerprint=manifest.schema_fingerprint,
            bindings=mapping.bindings * 2,
        )

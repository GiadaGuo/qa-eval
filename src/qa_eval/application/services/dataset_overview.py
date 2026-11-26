"""Read the published v3 metadata without opening the record stream."""

import hashlib
import json

from qa_eval.application.normalization.record_reader import RawRecordManifest
from qa_eval.contracts.logical_inputs import MappingRevision
from qa_eval.infrastructure.db.models.mapping_revision import MappingRevisionModel


def dataset_overview(session, version, dataset):
    refs = version.artifact_refs or {}
    if "raw_record_manifest" not in refs:
        return None
    manifest = RawRecordManifest.model_validate(refs["raw_record_manifest"])
    raw = refs.get("raw_file") or {}
    digest = version.default_mapping_digest
    row = session.get(MappingRevisionModel, (version.id, digest)) if digest else None
    mapping = MappingRevision.model_validate(row.document) if row else None
    identity = {
        "schema_version": manifest.schema_version,
        "data": manifest.source_sha256,
        "schema": manifest.schema_fingerprint,
        "mapping": digest,
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {
        "sample_record_schema": manifest.schema_version,
        "record_count": raw.get("record_count"),
        "size_bytes": raw.get("size_bytes"),
        "modalities": sorted(dataset.modality),
        "fields": [
            {key: field.get(key) for key in ("name", "data_type", "nullable")}
            for field in raw.get("fields", [])
        ],
        "semantics": [
            binding.model_dump(mode="json", exclude={"definition"})
            for binding in mapping.bindings
        ]
        if mapping
        else [],
        "artifact_kinds": sorted(
            kind
            for key, kind in {
                "raw_record_manifest": "RAW_RECORDS",
                "manifest": "DATASET_MANIFEST",
                "kenlm_model": "KENLM_MODEL",
            }.items()
            if refs.get(key)
        ),
        "mapping_digest": digest,
        "fingerprint": "sha256:" + hashlib.sha256(serialized.encode()).hexdigest(),
    }

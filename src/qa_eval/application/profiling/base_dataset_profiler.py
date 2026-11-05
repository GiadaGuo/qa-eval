from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime

from qa_eval.contracts import DatasetManifestV2, DatasetProfileV1, SampleRecordV2
from qa_eval.infrastructure.uuid7 import uuid7

PROFILER_KEY = "base_dataset_profiler"
PROFILER_VERSION = "1.0.0"


class BaseDatasetProfiler:
    """Full-scan, deterministic profiler over canonical SampleRecord 2.0."""

    config_fingerprint = (
        "sha256:" + hashlib.sha256(b"base-dataset-profiler:1.0.0:full-scan").hexdigest()
    )

    def profile(
        self,
        manifest: DatasetManifestV2,
        records: Iterable[SampleRecordV2],
        *,
        normalized_size_bytes: int,
    ) -> DatasetProfileV1:
        sample_count = 0
        role_counts: dict[str, int] = {}
        presence: dict[str, dict[str, int]] = {}
        lengths: list[int] = []
        modalities: set[str] = set()
        for record in records:
            sample_count += 1
            for content in record.contents:
                modalities.add(content.modality)
                role_counts[content.role] = role_counts.get(content.role, 0) + 1
                counts = presence.setdefault(
                    content.role, {"PRESENT": 0, "NULL": 0, "MISSING": 0, "EMPTY": 0}
                )
                counts[content.presence] += 1
                if (
                    content.presence == "PRESENT"
                    and getattr(content.payload, "type", None) == "inline_text"
                ):
                    text = content.payload.text
                    lengths.append(len(text))
                    if text == "":
                        counts["EMPTY"] += 1
        ordered = sorted(lengths)
        percentile = lambda p: (
            ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
            if ordered
            else None
        )
        now = datetime.now(UTC)
        languages = manifest.declared_context.languages
        language_distribution = (
            {value: 1 / len(languages) for value in languages} if languages else {}
        )
        rejected = next(
            (
                a.record_count or 0
                for a in manifest.artifacts
                if a.kind == "REJECTED_RECORDS"
            ),
            0,
        )
        field_profiles = [
            {
                "field": role,
                "modality": "text",
                "present_count": counts["PRESENT"],
                "null_count": counts["NULL"],
                "missing_count": counts["MISSING"],
                "empty_count": counts["EMPTY"],
                "total_count": sum(
                    counts[key] for key in ("PRESENT", "NULL", "MISSING")
                ),
            }
            for role, counts in sorted(presence.items())
        ]
        return DatasetProfileV1.model_validate(
            {
                "schema_version": "1.0",
                "profile_id": uuid7(),
                "dataset_version_id": manifest.dataset_version_id,
                "profiler": {
                    "key": PROFILER_KEY,
                    "version": PROFILER_VERSION,
                    "config_fingerprint": self.config_fingerprint,
                },
                "status": "OK",
                "summary": {
                    "source_record_count": manifest.source_snapshot.record_count,
                    "normalized_record_count": sample_count,
                    "rejected_record_count": rejected,
                },
                "identity": {
                    "sample_count": sample_count,
                    "format": "jsonl",
                    "size_bytes": normalized_size_bytes,
                },
                "structure": {
                    "modalities": sorted(modalities),
                    "content_roles": sorted(role_counts),
                    "schema_summary": {
                        "sample_record_schema": "2.0",
                        "role_counts": role_counts,
                    },
                    "sample_granularity": "record",
                },
                "statistics": {
                    "missingness": presence,
                    "text_length": {
                        "unit": "characters",
                        "count": len(lengths),
                        "min": min(lengths) if lengths else None,
                        "max": max(lengths) if lengths else None,
                        "mean": sum(lengths) / len(lengths) if lengths else None,
                        "p50": percentile(0.50),
                        "p95": percentile(0.95),
                    },
                },
                "content_features": {
                    "language_distribution": language_distribution,
                    "language_source": "DECLARED" if languages else "UNKNOWN",
                },
                "application_context": {
                    "task_types": manifest.declared_context.task_types,
                    **manifest.declared_context.attributes,
                    "source": "user_declared",
                },
                "profiling_metadata": {"method": "FULL_SCAN", "generated_at": now},
                "field_profiles": field_profiles,
                "artifacts": [],
                "created_at": now,
            }
        )

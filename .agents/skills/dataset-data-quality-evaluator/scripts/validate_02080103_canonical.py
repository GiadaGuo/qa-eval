"""Validate a canonical 02080103 local run and its artifact integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from qa_eval.contracts.datasets import (
    DatasetManifestV2,
    RejectedRecordV1,
    SampleRecordV2,
)
from qa_eval.contracts.metrics import MetricResultV1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _schema(repository_root: Path, relative_path: str) -> dict[str, Any]:
    return json.loads((repository_root / "schemas/qa-eval" / relative_path).read_text())


def validate(
    repository_root: Path,
    run_dir: Path,
    legacy_metric_result_path: Path,
) -> dict[str, Any]:
    manifest_data = json.loads((run_dir / "dataset_manifest.json").read_text())
    result_path = run_dir / "metrics/02080103-v2.0.0/metric_result.json"
    result_data = json.loads(result_path.read_text())
    manifest = DatasetManifestV2.model_validate(manifest_data)
    result = MetricResultV1.model_validate(result_data)

    for relative_schema, payload in [
        ("dataset-manifest/2.0.schema.json", manifest_data),
        ("metric-result/1.0.schema.json", result_data),
    ]:
        Draft202012Validator(
            _schema(repository_root, relative_schema),
            format_checker=FormatChecker(),
        ).validate(payload)

    normalized_count = 0
    with (run_dir / "sample_records.jsonl").open(encoding="utf-8") as source:
        for line in source:
            SampleRecordV2.model_validate_json(line)
            normalized_count += 1
    rejected_count = 0
    with (run_dir / "rejected_records.jsonl").open(encoding="utf-8") as source:
        for line in source:
            RejectedRecordV1.model_validate_json(line)
            rejected_count += 1

    evidence_schema_paths = {
        "qa-eval://schemas/evidence/02080103-sample-features/2.0.0": (
            repository_root
            / ".agents/skills/dataset-data-quality-evaluator/assets/schemas/02080103-sample-features-2.0.0.schema.json"
        ),
        "qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0": (
            repository_root
            / ".agents/skills/dataset-data-quality-evaluator/assets/schemas/02080103-duplicate-groups-2.0.0.schema.json"
        ),
    }
    artifact_checks = []
    for evidence in result.evidence:
        artifact_path = run_dir / evidence.artifact.uri
        actual_checksum = _sha256(artifact_path)
        if actual_checksum != evidence.artifact.checksum.value:
            raise ValueError(f"evidence checksum mismatch: {artifact_path}")
        evidence_schema = json.loads(
            evidence_schema_paths[evidence.schema_ref].read_text()
        )
        evidence_validator = Draft202012Validator(evidence_schema)
        if artifact_path.suffix == ".jsonl":
            evidence_record_count = 0
            with artifact_path.open(encoding="utf-8") as source:
                for line in source:
                    evidence_validator.validate(json.loads(line))
                    evidence_record_count += 1
        else:
            evidence_validator.validate(json.loads(artifact_path.read_text()))
            evidence_record_count = 1
        if evidence_record_count != evidence.artifact.record_count:
            raise ValueError(f"evidence record_count mismatch: {artifact_path}")
        artifact_checks.append(
            {
                "uri": evidence.artifact.uri,
                "checksum_verified": True,
                "schema_verified": True,
                "record_count_verified": True,
            }
        )

    if any(
        UUID(str(identifier)).version != 7 for identifier in [result.metric_result_id]
    ):
        raise ValueError("new result identifiers must be UUIDv7")
    for evidence in result.evidence:
        if UUID(str(evidence.evidence_id)).version != 7:
            raise ValueError("new evidence identifiers must be UUIDv7")
        if UUID(str(evidence.artifact.artifact_id)).version != 7:
            raise ValueError("new artifact identifiers must be UUIDv7")

    legacy = json.loads(legacy_metric_result_path.read_text())
    observations = {item.key: item for item in result.observations}
    regression_pairs = {
        "exact_affected_record_ratio": "affected_record_ratio",
        "exact_redundant_record_ratio": "redundant_record_ratio",
        "char_ngram_repetition_ratio": "char_rep_ratio",
        "word_ngram_repetition_ratio": "word_rep_ratio",
        "near_duplicate_candidate_record_count": (
            "minhash.near_duplicate_record_count"
        ),
        "paragraph_affected_ratio": "paragraph_affected_ratio",
    }
    for observation_key, legacy_key in regression_pairs.items():
        legacy_value: Any = legacy
        for part in legacy_key.split("."):
            legacy_value = legacy_value[part]
        if observations[observation_key].value != legacy_value:
            raise ValueError(f"regression mismatch: {observation_key}")

    if normalized_count + rejected_count != manifest.source_snapshot.record_count:
        raise ValueError("normalization counts do not close to manifest source count")
    if result.population is None:
        raise ValueError("02080103 requires population")
    if result.population.input_count != normalized_count:
        raise ValueError("MetricResult population does not match normalized input")

    return {
        "status": "PASS",
        "dataset_version_id": str(manifest.dataset_version_id),
        "metric_result_id": str(result.metric_result_id),
        "counts": {
            "source": manifest.source_snapshot.record_count,
            "normalized": normalized_count,
            "rejected": rejected_count,
            "evaluated": result.population.evaluated_count,
            "excluded": result.population.excluded_count,
        },
        "artifact_checks": artifact_checks,
        "legacy_numeric_regression": "PASS",
        "pydantic_validation": "PASS",
        "json_schema_validation": "PASS",
        "uuid_version_validation": "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("legacy_metric_result", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary = validate(args.repository_root, args.run_dir, args.legacy_metric_result)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

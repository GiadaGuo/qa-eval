"""Complete 02080103 from MetricResult to canonical QualityReport."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qa_eval.application.evaluation import (
    MetricCatalogEntry,
    build_quality_report,
    evaluate_contract,
)
from qa_eval.application.identifiers import uuid7
from qa_eval.contracts.artifacts import ArtifactRef
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
)

METRIC_KEY = "02080103"
METRIC_VERSION = "1.0.0"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[Any]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _duplicate_evidence(result: MetricResultV1):
    matches = [item for item in result.evidence if item.kind == "DUPLICATE_GROUPS"]
    if len(matches) != 1:
        raise ValueError(
            "MetricResult must reference exactly one DUPLICATE_GROUPS evidence"
        )
    return matches[0]


def _issue_source(result: MetricResultV1, measurement: str) -> dict[str, Any]:
    return {
        "metric_result_id": result.metric_result_id,
        "metric_key": result.metric.key,
        "metric_version": result.metric.version,
        "measurement": measurement,
        "rule_evaluation_id": None,
    }


def _generate_issues(
    result: MetricResultV1,
    duplicate_groups: dict[str, Any],
    *,
    evidence_id,
    created_at: datetime,
) -> list[QualityIssueV1]:
    issues: list[QualityIssueV1] = []
    for group in duplicate_groups["groups"]:
        representative = group["representative_sample_id"]
        for sample_id in group["sample_ids"]:
            if sample_id == representative:
                continue
            issues.append(
                QualityIssueV1.model_validate(
                    {
                        "schema_version": "1.0",
                        "issue_id": uuid7(),
                        "run_id": result.run_id,
                        "dataset_version_id": result.dataset_version_id,
                        "source": _issue_source(result, "exact_redundant_record_ratio"),
                        "detector": {
                            "key": "exact_duplicate_redundant_sample",
                            "version": "1.0.0",
                        },
                        "category": "DUPLICATE_CONTENT",
                        "code": "EXACT_DUPLICATE_REDUNDANT",
                        "determination": "CONFIRMED",
                        "severity": "WARNING",
                        "scope": "SAMPLE",
                        "subject": {"type": "SAMPLE", "sample_id": sample_id},
                        "related_subjects": [
                            {
                                "relation": "DUPLICATE_OF",
                                "subject": {
                                    "type": "SAMPLE",
                                    "sample_id": representative,
                                },
                            }
                        ],
                        "message": "该样本与重复组代表样本的规范化正文完全相同。",
                        "evidence": [
                            {
                                "evidence_id": evidence_id,
                                "locator": {
                                    "group_id": group["group_id"],
                                    "normalized_sha256": group["normalized_sha256"],
                                },
                            }
                        ],
                        "dedup_key": (
                            f"{METRIC_KEY}:EXACT_DUPLICATE_REDUNDANT:"
                            f"{group['group_id']}:{sample_id}"
                        ),
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
            )
    for group in duplicate_groups["near_duplicate_groups"]:
        representative = group["sample_ids"][0]
        for sample_id in group["sample_ids"]:
            related = []
            if sample_id != representative:
                related.append(
                    {
                        "relation": "NEAR_DUPLICATE_GROUP_REPRESENTATIVE",
                        "subject": {"type": "SAMPLE", "sample_id": representative},
                    }
                )
            issues.append(
                QualityIssueV1.model_validate(
                    {
                        "schema_version": "1.0",
                        "issue_id": uuid7(),
                        "run_id": result.run_id,
                        "dataset_version_id": result.dataset_version_id,
                        "source": _issue_source(
                            result, "near_duplicate_candidate_record_count"
                        ),
                        "detector": {
                            "key": "near_duplicate_candidate_sample",
                            "version": "1.0.0",
                        },
                        "category": "DUPLICATE_CONTENT",
                        "code": "NEAR_DUPLICATE_CANDIDATE",
                        "determination": "CANDIDATE",
                        "severity": "INFO",
                        "scope": "SAMPLE",
                        "subject": {"type": "SAMPLE", "sample_id": sample_id},
                        "related_subjects": related,
                        "message": "该样本由 MinHash + LSH 召回为近重复候选，尚需复核。",
                        "evidence": [
                            {
                                "evidence_id": evidence_id,
                                "locator": {"group_id": group["group_id"]},
                            }
                        ],
                        "dedup_key": (
                            f"{METRIC_KEY}:NEAR_DUPLICATE_CANDIDATE:"
                            f"{group['group_id']}:{sample_id}"
                        ),
                        "status": "OPEN",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
            )
    dedup_keys = [item.dedup_key for item in issues]
    if len(dedup_keys) != len(set(dedup_keys)):
        raise ValueError("generated QualityIssue dedup_key values must be unique")
    return issues


def complete(
    canonical_run_dir: Path,
    contract_path: Path,
    output_dir: Path,
    *,
    execution_status: str,
    execution_authority: str,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    contract = QualityContractVersionV1.model_validate_json(contract_path.read_text())
    if contract.status != "PUBLISHED":
        raise ValueError("executor requires a PUBLISHED QualityContractVersion")
    result_path = canonical_run_dir / "metrics/02080103-v2.0.0/metric_result.json"
    result = MetricResultV1.model_validate_json(result_path.read_text())
    if result.metric.key != METRIC_KEY or result.metric.version != METRIC_VERSION:
        raise ValueError("executor only supports 02080103 metric version 1.0.0")
    relevant_items = [
        item
        for item in contract.items
        if item.metric.key == METRIC_KEY and item.metric.version == METRIC_VERSION
    ]
    if not relevant_items:
        raise ValueError("contract does not contain a 02080103 item")
    if len(relevant_items) != len(contract.items):
        raise ValueError(
            "this single-metric executor cannot complete unrelated contract items"
        )

    duplicate_evidence = _duplicate_evidence(result)
    evidence_path = canonical_run_dir / duplicate_evidence.artifact.uri
    if _sha256(evidence_path) != duplicate_evidence.artifact.checksum.value:
        raise ValueError("DUPLICATE_GROUPS evidence checksum mismatch")
    duplicate_groups = json.loads(evidence_path.read_text(encoding="utf-8"))
    created_at = datetime.now(UTC)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent)
    )
    try:
        evaluations = evaluate_contract(
            contract,
            [result],
            id_factory=uuid7,
            evaluated_at=created_at,
        )
        issues = _generate_issues(
            result,
            duplicate_groups,
            evidence_id=duplicate_evidence.evidence_id,
            created_at=created_at,
        )
        _write_json(
            staging_dir / "quality_contract.json",
            contract.model_dump(mode="json", exclude_none=True),
        )
        _write_jsonl(
            staging_dir / "rule_evaluations.jsonl",
            [item.model_dump(mode="json", exclude_none=True) for item in evaluations],
        )
        issues_path = staging_dir / "issues.jsonl"
        _write_jsonl(
            issues_path,
            [item.model_dump(mode="json", exclude_none=True) for item in issues],
        )
        counts = Counter(item.determination for item in issues)
        issue_artifact = ArtifactRef.model_validate(
            {
                "artifact_id": uuid7(),
                "kind": "ISSUE_INDEX",
                "uri": "issues.jsonl",
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/quality-issue/1.0.schema.json",
                "size_bytes": issues_path.stat().st_size,
                "record_count": len(issues),
                "checksum": {"algorithm": "sha256", "value": _sha256(issues_path)},
            }
        )
        issue_index = IssueIndexV1.model_validate(
            {
                "schema_version": "1.0",
                "issue_index_id": uuid7(),
                "run_id": result.run_id,
                "dataset_version_id": result.dataset_version_id,
                "artifact": issue_artifact.model_dump(mode="json", exclude_none=True),
                "supported_filters": [
                    "metric_key",
                    "code",
                    "determination",
                    "severity",
                    "scope",
                    "sample_id",
                ],
                "summary": {
                    "confirmed_count": counts["CONFIRMED"],
                    "candidate_count": counts["CANDIDATE"],
                },
            }
        )
        _write_json(
            staging_dir / "issue_index.json",
            issue_index.model_dump(mode="json", exclude_none=True),
        )
        report = build_quality_report(
            contract,
            [result],
            evaluations,
            issues,
            {result.metric_result_id: issue_index},
            [
                MetricCatalogEntry(
                    key=METRIC_KEY,
                    version=METRIC_VERSION,
                    name="重复内容程度",
                    dimension="DATA_QUALITY",
                )
            ],
            execution_status=execution_status,
            id_factory=uuid7,
            generated_at=created_at,
        )
        _write_json(
            staging_dir / "quality_report.json",
            report.model_dump(mode="json", exclude_none=True),
        )
        summary = {
            "status": "PASS",
            "contract_version_id": str(contract.contract_version_id),
            "metric_result_id": str(result.metric_result_id),
            "rule_evaluation_count": len(evaluations),
            "confirmed_issue_count": counts["CONFIRMED"],
            "candidate_issue_count": counts["CANDIDATE"],
            "issue_index_id": str(issue_index.issue_index_id),
            "report_id": str(report.report_id),
            "gate_status": report.gate_status,
            "execution_authority": execution_authority,
            "not_a_platform_run": execution_authority != "PLATFORM",
        }
        _write_json(staging_dir / "completion_summary.json", summary)
        staging_dir.rename(output_dir)
        return summary
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_run_dir", type=Path)
    parser.add_argument("contract", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--execution-status",
        required=True,
        choices=(
            "PENDING",
            "RUNNING",
            "SUCCEEDED",
            "PARTIAL_SUCCEEDED",
            "FAILED",
            "CANCELED",
        ),
        help="status supplied by the EvaluationRun owner; never inferred from quality",
    )
    parser.add_argument(
        "--execution-authority",
        required=True,
        choices=("LOCAL_VALIDATION", "PLATFORM"),
        help="marks whether the referenced run IDs are platform-persisted",
    )
    args = parser.parse_args(argv)
    summary = complete(
        args.canonical_run_dir,
        args.contract,
        args.output_dir,
        execution_status=args.execution_status,
        execution_authority=args.execution_authority,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

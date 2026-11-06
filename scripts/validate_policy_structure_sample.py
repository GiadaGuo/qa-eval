"""Run the production Calculator on the fixed Phase-B sample without full scan."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

from qa_eval.calculators.policy_structure_compliance import (
    PolicyStructureComplianceCalculator,
)
from qa_eval.calculators.policy_structure_compliance.parsers import parse_structure
from qa_eval.calculators.protocol import CalculatorContext


def validate(profile_path: Path, records_path: Path) -> dict:
    profile = json.loads(profile_path.read_text())
    expected = {
        (item["line_number"], item["sample_id"])
        for stratum in profile["stratified_samples"].values()
        for item in stratum["samples"]
    }
    wanted_lines = {line for line, _sample_id in expected}
    expected_by_line = dict(expected)
    version_id: UUID | None = None
    benchmark_inputs: list[tuple[str, str, dict]] = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path = root / "selected.jsonl"
        selected = 0
        with (
            records_path.open(encoding="utf-8") as source,
            selected_path.open("w", encoding="utf-8") as destination,
        ):
            for line_number, line in enumerate(source, 1):
                if line_number not in wanted_lines:
                    continue
                record = json.loads(line)
                if record["sample_id"] != expected_by_line[line_number]:
                    raise ValueError(f"sample ID mismatch at line {line_number}")
                version_id = UUID(record["dataset_version_id"])
                by_role = {
                    item["role"]: item.get("payload", {}).get("text", "")
                    for item in record["contents"]
                    if item.get("presence") == "PRESENT"
                }
                benchmark_inputs.append(
                    (
                        by_role.get("article.body.html", ""),
                        by_role.get("article.title", ""),
                        record.get("metadata", {}),
                    )
                )
                destination.write(line)
                selected += 1
        if selected != len(expected):
            raise ValueError(f"selected {selected} records, expected {len(expected)}")
        assert version_id is not None
        context = CalculatorContext(
            run_id=uuid4(),
            stage_run_id=uuid4(),
            dataset_version_id=version_id,
            metric_key="0204",
            metric_version="2.0.0",
            input_path=selected_path,
            output_dir=root / "output",
            input_schema_ref="2.0",
        )
        tracemalloc.start()
        started = time.perf_counter()
        result = PolicyStructureComplianceCalculator().execute(context, {})
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows = [
            json.loads(line)
            for line in result.evidence[0].path.read_text().splitlines()
        ]
        length_benchmarks = {}
        for name, lower, upper in (
            ("0-10k", 0, 10_000),
            ("10k-100k", 10_000, 100_000),
            ("100k-300k", 100_000, 300_000),
            ("over-300k", 300_000, 10**20),
        ):
            values = [
                item for item in benchmark_inputs if lower <= len(item[0]) < upper
            ]
            bucket_started = time.perf_counter()
            for html_source, title, metadata in values:
                parse_structure(
                    html_source=html_source,
                    plain_source=None,
                    title=title,
                    metadata=metadata,
                )
            bucket_elapsed = time.perf_counter() - bucket_started
            length_benchmarks[name] = {
                "sample_count": len(values),
                "total_html_chars": sum(len(item[0]) for item in values),
                "elapsed_seconds": bucket_elapsed,
                "samples_per_second": len(values) / bucket_elapsed if values else None,
            }
    modes = Counter(row.get("mode", "UNAVAILABLE") for row in rows)
    document_types = Counter(
        row.get("document_type", {}).get("value", "UNAVAILABLE") for row in rows
    )
    reasons = {
        flag: Counter(row["flags"][flag]["reason_code"] for row in rows)
        for flag in (
            "parsing_success",
            "numbering_valid",
            "segmentation_correct",
            "path_locatable",
        )
    }
    statuses = {
        flag: Counter(row["flags"][flag]["status"] for row in rows) for flag in reasons
    }
    notifications = [
        row for row in rows if row.get("document_type", {}).get("value") == "通知"
    ]
    notification_modes = Counter(
        row.get("mode", "UNAVAILABLE") for row in notifications
    )
    expected_notification_ids = {
        item["sample_id"]
        for item in profile["stratified_samples"]["notification"]["samples"]
    }
    recognized_expected_notifications = sum(
        row["sample_id"] in expected_notification_ids
        and row.get("document_type", {}).get("value") == "通知"
        for row in rows
    )
    return {
        "schema_version": "1.0",
        "validation_kind": "FIXED_PHASE_B_SAMPLE_AUTOMATED_VALIDATION",
        "input": {
            "profile": str(profile_path),
            "normalized_records": str(records_path),
            "dataset_version_id": str(version_id),
            "sample_count": len(rows),
        },
        "result_status": result.status,
        "flag_status_counts": {key: dict(value) for key, value in statuses.items()},
        "reason_counts": {key: dict(value) for key, value in reasons.items()},
        "mode_counts": dict(modes),
        "document_type_counts": dict(document_types),
        "notification": {
            "sample_count": len(notifications),
            "mode_counts": dict(notification_modes),
            "phase_b_notification_stratum_count": len(expected_notification_ids),
            "phase_b_notification_stratum_recognized_count": recognized_expected_notifications,
            "interpretation": "Recognition against the Phase-B proxy stratum, not legal-document-type ground truth.",
        },
        "performance": {
            "elapsed_seconds": elapsed,
            "samples_per_second": len(rows) / elapsed,
            "tracemalloc_peak_bytes": peak,
            "evidence_size_bytes": sum(
                len(json.dumps(row, ensure_ascii=False).encode()) + 1 for row in rows
            ),
            "full_dataset_time_projection_seconds": 321_902 / (len(rows) / elapsed),
            "projection_warning": "Linear projection from a stratified sample; not a full-run measurement.",
            "parser_length_buckets_without_tracemalloc": length_benchmarks,
        },
        "coordinate_validation": {
            "path_pass_count": statuses["path_locatable"]["PASS"],
            "path_fail_count": statuses["path_locatable"]["FAIL"],
            "method": "Every PASS was checked against bounded ordered raw Unicode spans and visible-text round trip by the Calculator.",
        },
        "limitations": [
            "This is automated validation, not human precision/recall review.",
            "The Phase-B sample is stratified and not an unbiased estimate of the full dataset.",
            "Segmentation remains UNAVAILABLE without versioned independent ground truth.",
            "No full 321,902-record Calculator run was started.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.profile, args.records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

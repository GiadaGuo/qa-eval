"""Validate canonical Contract-to-QualityReport output and references."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)


def _schema(repository_root: Path, name: str) -> dict[str, Any]:
    return json.loads(
        (repository_root / f"schemas/qa-eval/{name}/1.0.schema.json").read_text()
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(repository_root: Path, output_dir: Path) -> dict[str, Any]:
    contract_data = json.loads((output_dir / "quality_contract.json").read_text())
    index_data = json.loads((output_dir / "issue_index.json").read_text())
    report_data = json.loads((output_dir / "quality_report.json").read_text())
    contract = QualityContractVersionV1.model_validate(contract_data)
    issue_index = IssueIndexV1.model_validate(index_data)
    report = QualityReportV1.model_validate(report_data)
    for name, payload in [
        ("quality-contract", contract_data),
        ("issue-index", index_data),
        ("quality-report", report_data),
    ]:
        Draft202012Validator(
            _schema(repository_root, name), format_checker=FormatChecker()
        ).validate(payload)

    rule_schema = Draft202012Validator(
        _schema(repository_root, "rule-evaluation"),
        format_checker=FormatChecker(),
    )
    evaluations = []
    for line in (output_dir / "rule_evaluations.jsonl").read_text().splitlines():
        payload = json.loads(line)
        rule_schema.validate(payload)
        evaluations.append(RuleEvaluationV1.model_validate(payload))

    issue_schema = Draft202012Validator(
        _schema(repository_root, "quality-issue"),
        format_checker=FormatChecker(),
    )
    issues = []
    issues_path = output_dir / "issues.jsonl"
    with issues_path.open(encoding="utf-8") as source:
        for line in source:
            payload = json.loads(line)
            issue_schema.validate(payload)
            issues.append(QualityIssueV1.model_validate(payload))

    if _sha256(issues_path) != issue_index.artifact.checksum.value:
        raise ValueError("IssueIndex artifact checksum mismatch")
    if issue_index.artifact.record_count != len(issues):
        raise ValueError("IssueIndex artifact record_count mismatch")
    counts = Counter(item.determination for item in issues)
    if issue_index.summary.confirmed_count != counts["CONFIRMED"]:
        raise ValueError("IssueIndex confirmed_count mismatch")
    if issue_index.summary.candidate_count != counts["CANDIDATE"]:
        raise ValueError("IssueIndex candidate_count mismatch")
    if report.contract_version_id != contract.contract_version_id:
        raise ValueError("QualityReport contract_version_id mismatch")
    if report.issue_summary.confirmed_count != counts["CONFIRMED"]:
        raise ValueError("QualityReport confirmed_count mismatch")
    if report.issue_summary.candidate_count != counts["CANDIDATE"]:
        raise ValueError("QualityReport candidate_count mismatch")
    evaluation_ids = {item.rule_evaluation_id for item in evaluations}
    report_evaluation_ids = {
        item.rule_evaluation_id
        for metric in report.metrics
        for item in metric.rule_evaluations
    }
    if evaluation_ids != report_evaluation_ids:
        raise ValueError("QualityReport RuleEvaluation references mismatch")

    return {
        "status": "PASS",
        "contract_version_id": str(contract.contract_version_id),
        "report_id": str(report.report_id),
        "gate_status": report.gate_status,
        "rule_evaluation_count": len(evaluations),
        "confirmed_issue_count": counts["CONFIRMED"],
        "candidate_issue_count": counts["CANDIDATE"],
        "pydantic_validation": "PASS",
        "json_schema_validation": "PASS",
        "checksum_validation": "PASS",
        "reference_validation": "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary = validate(args.repository_root, args.output_dir)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

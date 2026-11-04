"""Audit legacy PostgreSQL and rebuild only workflow-critical local data.

Historical datasets, runs, reports, and agent sessions are intentionally not
copied when their referenced artifacts are unavailable. The executable metric
registry is rebuilt from the repository's canonical configs instead of copying
potentially stale database documents or code digests.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from qa_eval.application.services.metric_registry_service import (
    MetricRegistryService,
)
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel
from qa_eval.infrastructure.db.models.registry import (
    CalculatorImplementationModel,
    MetricCalculatorBindingModel,
    MetricDefinitionModel,
)
from qa_eval.infrastructure.db.session import create_database_engine
from qa_eval.paths import resolve_project_path

HEAD_REVISION = "c9f4a21d8e73"
REGISTRY_TABLES = (
    "metric_definitions",
    "calculator_implementations",
    "metric_calculator_bindings",
)
EXCLUDED_TABLE_REASONS = {
    "datasets": "dataset versions depend on unavailable artifacts",
    "dataset_versions": "all 27 referenced artifacts are unavailable",
    "dataset_manifests": "derived from excluded dataset versions",
    "dataset_profiles": "derived from excluded dataset versions",
    "quality_contract_versions": "contracts target excluded dataset versions",
    "logical_evaluation_plans": "historical execution state",
    "physical_evaluation_plans": "historical execution state",
    "evaluation_runs": "historical execution state",
    "stage_runs": "historical execution state",
    "metric_results": "historical execution result",
    "rule_evaluations": "historical quality result",
    "quality_issues": "historical quality result",
    "issue_indices": "historical quality result",
    "quality_reports": "historical quality result with unavailable artifacts",
    "scored_quality_reports": "historical derived report",
    "document_versions": "source document artifact is unavailable",
    "document_report_tasks": "depends on excluded document versions",
    "document_quality_reports": "depends on excluded document versions",
    "agent_sessions": "historical session targeting excluded dataset versions",
    "conversation_messages": "historical agent audit trail",
    "skill_invocations": "historical agent audit trail",
    "tool_invocations": "historical agent audit trail",
    "proposal_revisions": "historical proposal targeting excluded versions",
}


def _row_counts(engine: Engine) -> dict[str, int]:
    metadata = MetaData()
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table_name in sorted(inspect(connection).get_table_names()):
            if table_name == "alembic_version":
                continue
            table = Table(table_name, metadata, autoload_with=connection)
            counts[table_name] = connection.scalar(
                select(func.count()).select_from(table)
            )
    return counts


def _revision(engine: Engine) -> str | None:
    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def _registry_keys(engine: Engine) -> dict[str, list[str]]:
    with Session(engine) as session:
        metrics = [
            f"{row.metric_key}@{row.version}"
            for row in session.scalars(
                select(MetricDefinitionModel).order_by(
                    MetricDefinitionModel.metric_key,
                    MetricDefinitionModel.version,
                )
            )
        ]
        calculators = [
            f"{row.calculator_key}@{row.version}"
            for row in session.scalars(
                select(CalculatorImplementationModel).order_by(
                    CalculatorImplementationModel.calculator_key,
                    CalculatorImplementationModel.version,
                )
            )
        ]
        bindings = [
            f"{row.metric_key}@{row.metric_version}@"
            f"{row.calculator_key}@{row.calculator_version}"
            for row in session.scalars(
                select(MetricCalculatorBindingModel).order_by(
                    MetricCalculatorBindingModel.metric_key,
                    MetricCalculatorBindingModel.calculator_key,
                )
            )
        ]
    return {
        "metrics": metrics,
        "calculators": calculators,
        "bindings": bindings,
    }


def _artifact_audit(source: Engine, artifact_root: Path) -> dict[str, Any]:
    refs: set[str] = set()
    with Session(source) as session:
        for artifact_refs in session.scalars(
            select(DatasetVersionModel.artifact_refs)
        ):
            for value in (artifact_refs or {}).values():
                if isinstance(value, dict) and value.get("uri"):
                    refs.add(value["uri"])
        for version in session.scalars(select(DocumentVersionModel)):
            refs.add(version.storage_uri)
            parsed_uri = (version.document or {}).get("parsed_artifact_uri")
            if parsed_uri:
                refs.add(parsed_uri)

    local_refs = sorted(uri for uri in refs if not uri.startswith("s3://"))
    s3_refs = sorted(uri for uri in refs if uri.startswith("s3://"))
    present = [uri for uri in local_refs if (artifact_root / uri).is_file()]
    missing = [uri for uri in local_refs if not (artifact_root / uri).is_file()]
    return {
        "total": len(refs),
        "local": len(local_refs),
        "s3": len(s3_refs),
        "present": len(present),
        "missing": len(missing),
        "all_referenced_artifacts_available": not missing and not s3_refs,
    }


def migrate(
    source: Engine,
    target: Engine,
    *,
    artifact_root: Path,
    apply: bool,
) -> dict[str, Any]:
    if source.dialect.name != "postgresql":
        raise ValueError("source database must be PostgreSQL")
    if target.dialect.name != "sqlite":
        raise ValueError("target database must be SQLite")

    source_counts = _row_counts(source)
    target_counts_before = _row_counts(target)
    source_revision = _revision(source)
    target_revision = _revision(target)
    if target_revision != HEAD_REVISION:
        raise ValueError(
            f"target revision must be {HEAD_REVISION}, got {target_revision}"
        )

    nonempty_target = {
        table: count for table, count in target_counts_before.items() if count
    }
    source_registry = _registry_keys(source)
    artifact_audit = _artifact_audit(source, artifact_root)

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if apply else "dry-run",
        "source": {
            "dialect": source.dialect.name,
            "revision": source_revision,
            "row_counts": source_counts,
        },
        "target": {
            "dialect": target.dialect.name,
            "revision": target_revision,
            "row_counts_before": target_counts_before,
        },
        "selection": {
            "strategy": "rebuild workflow registry from canonical configs",
            "tables": list(REGISTRY_TABLES),
            "excluded_tables": {
                table: {
                    "row_count": source_counts.get(table, 0),
                    "reason": reason,
                }
                for table, reason in EXCLUDED_TABLE_REASONS.items()
            },
        },
        "artifact_audit": artifact_audit,
        "validation": {
            "target_was_empty": not nonempty_target,
            "nonempty_target_tables": nonempty_target,
        },
    }

    if not apply:
        return report
    if nonempty_target:
        raise ValueError(
            "target contains business data; recreate the SQLite database before apply"
        )

    with Session(target) as session:
        sync_result = MetricRegistryService(session).sync()
        session.commit()

    target_registry = _registry_keys(target)
    comparison = {}
    for group in ("metrics", "calculators", "bindings"):
        source_keys = set(source_registry[group])
        target_keys = set(target_registry[group])
        comparison[group] = {
            "source_count": len(source_keys),
            "target_count": len(target_keys),
            "source_only": sorted(source_keys - target_keys),
            "target_only": sorted(target_keys - source_keys),
        }

    with target.connect() as connection:
        integrity_check = connection.scalar(text("PRAGMA integrity_check"))
        foreign_key_errors = connection.execute(
            text("PRAGMA foreign_key_check")
        ).all()

    report["target"]["row_counts_after"] = _row_counts(target)
    report["registry_sync"] = sync_result
    report["validation"].update(
        {
            "registry_key_comparison": comparison,
            "integrity_check": integrity_check,
            "foreign_key_error_count": len(foreign_key_errors),
            "success": (
                integrity_check == "ok"
                and not foreign_key_errors
                and all(
                    not result["source_only"] and not result["target_only"]
                    for result in comparison.values()
                )
            ),
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target-url",
        default="sqlite:///data/runtime/qa-eval.sqlite3",
    )
    parser.add_argument(
        "--artifact-root",
        default="data/artifacts",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    source = create_engine(get_settings().database_url, pool_pre_ping=True)
    target = create_database_engine(args.target_url)
    try:
        report = migrate(
            source,
            target,
            artifact_root=resolve_project_path(args.artifact_root),
            apply=args.apply,
        )
    finally:
        source.dispose()
        target.dispose()

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("validation", {}).get("success", not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())

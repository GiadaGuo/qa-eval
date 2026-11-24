"""Run 0201 against published normalized data in a fresh local SQLite sandbox."""

from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.calculators.policy_document_format.calculator import (
    KEY,
    record_input,
    settings,
)
from qa_eval.calculators.policy_document_format.matching import inspect
from qa_eval.calculators.policy_document_format.scoring import score_document
from qa_eval.contracts.datasets import DatasetManifestV2, SampleRecordV2
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
    _sha256_file,
)

DOCS = Path(__file__).resolve().parents[1] / "docs/development/0201-format"


def reviewed_samples():
    with (DOCS / "phase06_samples.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        groups = {row["iid"]: "phase06" for row in csv.DictReader(stream)}
    for line in (DOCS / "phase05_samples.jsonl").read_bytes().splitlines():
        row = json.loads(line)
        if row["stratum"] in {"html_risk", "compound_notice", "type_boundary"}:
            groups[row["iid"]] = groups.get(row["iid"], "phase05:" + row["stratum"])
    return groups


def extract_samples(normalized, index, ids, destination):
    with (
        closing(
            sqlite3.connect(index.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as lookup,
        normalized.open("rb") as source,
        destination.open("wb") as output,
    ):
        metadata = json.loads(index.with_name("metadata.json").read_bytes())
        for iid in ids:
            match = lookup.execute(
                "SELECT byte_start, byte_length FROM records WHERE sample_id = ?",
                (iid,),
            ).fetchone()
            if not match:
                raise ValueError(f"published index does not contain {iid}")
            start, length = match
            source.seek(start)
            line = source.read(length)
            if json.loads(line)["sample_id"] != iid:
                raise ValueError(
                    "published index location disagrees with source record"
                )
            output.write(line.rstrip(b"\r\n") + b"\n")
    return metadata


def seed(session, store, source, manifest_path, count, full=False):
    manifest = DatasetManifestV2.model_validate_json(manifest_path.read_bytes())
    version_id = manifest.dataset_version_id
    base = next(a for a in manifest.artifacts if a.kind == "NORMALIZED_DATA")
    checksum = _sha256_file(source)
    if full and (base.checksum.value != checksum or base.record_count != count):
        raise ValueError("full input differs from the published normalized artifact")
    uri = "input/normalized.jsonl"
    target = store.root_dir / uri
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source.resolve())
    data_ref = {"uri": uri, "sha256": checksum}
    manifest_data = manifest.model_dump(mode="json")
    manifest_data["artifacts"] = [
        base.model_dump(mode="json")
        | {
            "artifact_id": str(uuid4()),
            "uri": uri,
            "record_count": count,
            "size_bytes": source.stat().st_size,
            "checksum": {"algorithm": "sha256", "value": checksum},
        }
    ]
    manifest_data["source_snapshot"]["record_count"] = count
    manifest_uri = "input/manifest.json"
    store.prepare_write(manifest_uri).write_text(
        json.dumps(manifest_data), encoding="utf-8"
    )
    local_manifest = store.commit(manifest_uri)
    session.add(
        DatasetModel(id=manifest.dataset_id, name=manifest.name, modality=["text"])
    )
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=manifest.dataset_id,
            version="phase-0201-isolated",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{checksum}",
            artifact_refs={
                "normalized": data_ref,
                "manifest": {"uri": manifest_uri, "sha256": local_manifest.sha256},
                "field_mapping": manifest_data["field_mapping"],
            },
        )
    )
    session.flush()
    MetricRegistryService(session).sync()
    return version_id


def run_isolated(output, source, manifest, count, full=False):
    engine = create_engine(f"sqlite:///{output / 'run.sqlite3'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(output / "artifacts")
    with Session(engine) as session:
        version = seed(session, store, source, manifest, count, full)
        key = "policy-format-review"
        contract = QualityContractService(session).publish_multi(
            QualityContractMultiPublishRequest.model_validate(
                {
                    "dataset_version_id": str(version),
                    "name": "0201 isolated validation",
                    "version_label": "1",
                    "items": [],
                    "observation_items": [
                        {
                            "schema_version": "1.1",
                            "key": key,
                            "strategy": "EVALUATE",
                            "metric_key": "0201",
                            "metric_version": "1.0.0",
                            "calculator_key": KEY,
                            "calculator_version": "1.0.0",
                            "parameters": {},
                            "projections": [],
                            "statistic_ref": {
                                "contract_item_key": key,
                                "output_slot_id": key,
                                "calculator_version": "1.0.0",
                                "statistics_key": "normative_violation_rate",
                            },
                            "rule": {"operator": "LTE", "threshold": 0},
                            "severity": "WARNING",
                        }
                    ],
                }
            )
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        service.claim_pending_run(run.run_id, worker_id="phase0201-isolated")
        started = time.perf_counter()
        completed = service.execute(run.run_id, worker_id="phase0201-isolated")
        elapsed = time.perf_counter() - started
        report = QualityResultService(session, store).finalize(run.run_id)
        [result] = service.list_calculator_results(run.run_id)
        output_data = {
            "run_id": str(run.run_id),
            "dataset_version_id": str(version),
            "run_status": completed.status,
            "error": completed.error,
            "result": result.model_dump(mode="json"),
            "seconds": elapsed,
            "records_per_second": result.statistics.get("input_count", 0) / elapsed,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if os.uname().sysname == "Darwin" else 1024),
            "report": report.model_dump(mode="json"),
        }
    (output / "run_summary.json").write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if completed.status != "SUCCEEDED":
        raise RuntimeError(
            f"isolated worker did not succeed, see {output / 'run_summary.json'}"
        )
    return output_data


def csv_text(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def review(source, groups, output):
    grid = {
        f"{t}/{c}": {"1": 0, "0": 0, "NOT_EVALUATED": 0}
        for t in (70, 80, 90)
        for c in (0.3, 0.5, 0.7)
    }
    reasons = Counter()
    bins = Counter()
    gates = Counter()
    fields = [
        "iid",
        "stratum",
        "type_candidate",
        "title",
        "excerpt",
        "score",
        "label",
        "coverage",
        "rules",
        "reasons",
        "review_flags",
        "manual_type",
        "manual_integrity",
        "manual_format",
        "manual_notes",
    ]
    with (
        (output / "phase3_review.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream,
        source.open("rb") as records,
    ):
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for raw in records:
            record = SampleRecordV2.model_validate_json(raw)
            row = record_input(record)
            doc = inspect(row)
            for threshold in (70, 80, 90):
                for coverage in (0.3, 0.5, 0.7):
                    label = score_document(
                        doc,
                        settings(
                            {"threshold": threshold, "minimum_coverage": coverage}
                        ),
                    )["label"]
                    grid[f"{threshold}/{coverage}"][str(label)] += 1
            default = score_document(doc, settings({}))
            main = default["segments"][0] if default["segments"] else {}
            reasons.update(default["reasons"])
            score = default["score"]
            score_band = (
                "unscored"
                if score is None
                else "<70"
                if score < 70
                else "70–79"
                if score < 80
                else "80–89"
                if score < 90
                else "90–100"
            )
            bins[score_band] += 1
            flags = list(default["reasons"])
            if score is not None and (score >= 95 or score < 82):
                flags.append("HIGH_SCORE" if score >= 95 else "NEAR_OR_BELOW_THRESHOLD")
            if main.get("score_bounds") and main["score_bounds"][0] < 70:
                flags.append("LOW_OBSERVED_LOWER_BOUND")
            if (
                main.get("independent_evidence_count", 0) <= 1
                and main.get("observed_score") == 100
            ):
                gates["single_anchor_observed_100"] += 1
                gates["single_anchor_accepted"] += default["label"] == 1
                flags.append("SINGLE_ANCHOR_OBSERVED_100")
            if any(
                word in row["title"]
                for word in ("批复意见", "初审意见", "审查意见", "关于修改", "关于修订")
            ):
                flags.append("TYPE_OR_AMENDMENT_REVIEW")
            if doc.get("attachment_status") not in {
                None,
                "NO_REQUIRED_ATTACHMENT_SIGNAL",
            }:
                flags.append("ATTACHMENT_REVIEW")
            anchors = doc["segments"][0]["anchors"] if doc["segments"] else []
            loc = next(
                (a["location"] for a in anchors if a["location"]["field"] == "content"),
                None,
            )
            excerpt = (
                row["content"][loc["start"] : min(loc["end"], loc["start"] + 100)]
                if loc
                else ""
            )
            writer.writerow(
                {
                    "iid": record.sample_id,
                    "stratum": groups[record.sample_id],
                    "type_candidate": doc["type_candidate"],
                    "title": csv_text(row["title"][:180]),
                    "excerpt": csv_text(excerpt),
                    "score": default["score"],
                    "label": default["label"],
                    "coverage": main.get("coverage"),
                    "rules": json.dumps(
                        [
                            {
                                "id": part["id"],
                                "path": score["path"],
                                "bounds": score["score_bounds"],
                                "structure": part["anchors"],
                                "normative": part["normative"],
                            }
                            for part, score in zip(
                                doc["segments"], default["segments"], strict=True
                            )
                        ],
                        ensure_ascii=False,
                    ),
                    "reasons": "|".join(default["reasons"]),
                    "review_flags": "|".join(flags),
                }
            )
    (output / "phase3_sensitivity.json").write_text(
        json.dumps(
            {
                "sample_count": len(groups),
                "grid": grid,
                "score_distribution": bins,
                "record_unknown_reasons": reasons,
                "evidence_gate": gates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if not args.full and not args.index:
        parser.error("subset validation requires --index")
    args.output.mkdir()
    if args.full:
        manifest = DatasetManifestV2.model_validate_json(args.manifest.read_bytes())
        run_isolated(
            args.output,
            args.normalized,
            args.manifest,
            next(
                a.record_count
                for a in manifest.artifacts
                if a.kind == "NORMALIZED_DATA"
            ),
            True,
        )
    else:
        target = args.output / "subset.jsonl"
        groups = reviewed_samples()
        extract_samples(args.normalized, args.index, groups, target)
        run_isolated(args.output, target, args.manifest, len(groups))
        review(target, groups, args.output)


if __name__ == "__main__":
    main()

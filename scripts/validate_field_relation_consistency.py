"""Validate 0207 from a published artifact in a fresh local SQLite store."""

import argparse
import csv
import json
import random
import resource
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.datasets import DatasetManifestV2
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
    _sha256_file,
)
from scripts.validate_policy_document_format import seed

SEED = 207
REFERENCE_PREFIX = 128
CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs/field_relation_consistency/policy.json"
)


def sample_input(source, artifact, index, destination, count):
    metadata = json.loads(index.with_name("metadata.json").read_bytes())
    if (
        metadata["source_checksum"] != f"sha256:{artifact.checksum.value}"
        or metadata["record_count"] != artifact.record_count
        or source.stat().st_size != artifact.size_bytes
        or metadata["index_checksum"] != f"sha256:{_sha256_file(index)}"
    ):
        raise ValueError(
            "published index or normalized input disagrees with the manifest"
        )
    selected = sorted(
        random.Random(SEED).sample(
            range(REFERENCE_PREFIX + 1, artifact.record_count + 1), count
        )
    )
    strata = {}
    with (
        closing(
            sqlite3.connect(index.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db,
        source.open("rb") as raw,
        destination.open("wb") as output,
    ):
        for number in selected:
            sample_id, line_no, start, length = db.execute(
                "SELECT sample_id, line_no, byte_start, byte_length FROM records WHERE rowid = ?",
                (number,),
            ).fetchone()
            raw.seek(start)
            line = raw.read(length)
            row = json.loads(line)
            if (
                line_no != number
                or row["sample_id"] != sample_id
                or row["source"]["source_row"] < REFERENCE_PREFIX
            ):
                raise ValueError("index row identity differs from selected holdout row")
            output.write(line.rstrip(b"\r\n") + b"\n")
            body = next(
                (
                    c.get("payload", {}).get("text", "")
                    for c in row["contents"]
                    if c.get("role") == "article.body.plain"
                ),
                "",
            )
            meta = row["metadata"]
            declared = meta.get("extra", {}).get("type")
            number_value = meta.get("document_number", {}).get("filenumber")
            strata[sample_id] = {
                "published_line": line_no,
                "source_category": declared,
                "body_characters": len(body),
                "number_present": bool(
                    number_value
                    and number_value.strip()
                    not in {"", "-", "None", "无", "001〔2022〕0728号"}
                ),
            }
    return strata


def write_review(store, artifact, strata, destination, size=100):
    groups = defaultdict(list)
    for row in EvidenceReader(store).iter_jsonl(artifact):
        sample = strata[row["sample_id"]]
        attributes = row["attributes"]
        edges = {edge["id"]: edge for edge in attributes["relations"]}
        category = sample["source_category"]
        if edges["number_body"]["confirmed_conflict"]:
            stratum = "confirmed_number_conflict"
        elif sample["body_characters"] < 40:
            stratum = "short_or_placeholder_body"
        elif category not in {"政策", "解读"}:
            stratum = "unverified_category"
        elif sample["body_characters"] > 12000:
            stratum = "long_body"
        elif not sample["number_present"]:
            stratum = "number_missing"
        else:
            stratum = "policy" if category == "政策" else "interpretation"
        groups[stratum].append(
            {
                "stratum": stratum,
                "sample_id": row["sample_id"],
                **sample,
                "record_label": row["status"],
                "record_reason": row["reason"],
                "record_coverage": attributes["coverage"],
                "record_support": attributes["score"],
                **{
                    name: f"{data['status']}/{data['reason']}"
                    for name, data in edges.items()
                },
                "topic_raw": edges["title_body_topic"]["raw_score"],
                "human_topic": "",
                "human_section_body": "",
                "human_section_title": "",
                "human_number": "",
                "review_notes": "",
            }
        )
    randomizer = random.Random(SEED)
    for group in groups.values():
        randomizer.shuffle(group)
    picked = []
    while len(picked) < size and any(groups.values()):
        for name in sorted(groups):
            if groups[name] and len(picked) < size:
                picked.append(groups[name].pop())
    with destination.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(picked[0]))
        writer.writeheader()
        writer.writerows(picked)
    return dict(Counter(row["stratum"] for row in picked))


def run(output, source, manifest, count, strata, full):
    engine = create_engine(f"sqlite:///{output / 'run.sqlite3'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(output / "artifacts")
    with Session(engine) as session:
        version = seed(session, store, source, manifest, count, full)
        contract = (
            QualityContractService(session)
            .publish_multi(
                QualityContractMultiPublishRequest.model_validate(
                    {
                        "dataset_version_id": str(version),
                        "name": "0207 isolated validation",
                        "version_label": "2.0",
                        "items": [
                            {
                                "key": "0207-coverage",
                                "metric_key": "0207",
                                "metric_version": "2.0.0",
                                "calculator_key": "field_relation_consistency",
                                "calculator_version": "2.0.0",
                                "parameters": json.loads(CONFIG.read_bytes()),
                                "measurement": "evaluable_record_ratio",
                                "rule": {
                                    "operator": "GTE",
                                    "threshold": 0.5,
                                    "severity": "INFO",
                                    "on_missing": "UNKNOWN",
                                },
                            }
                        ],
                    }
                )
            )
            .contract
        )
        service = EvaluationRunService(session, store)
        evaluation = service.create(contract.contract_version_id)
        session.commit()
        started = time.perf_counter()
        finished = service.execute(evaluation.run_id)
        seconds = time.perf_counter() - started
        if finished.status != "SUCCEEDED":
            raise RuntimeError(finished.error)
        [result] = service.list_calculator_results(finished.run_id)
        [artifact] = result.decision_artifact_refs
        if (
            result.statistics["input_count"] != count
            or artifact.summary.record_count != count
        ):
            raise RuntimeError("input and decision artifact counts differ")
        report = QualityResultService(session, store).finalize(evaluation.run_id)
        operational = QualityResultService(session, store).rebuild_operational_report(
            evaluation.run_id
        )
        axis = next(item for item in operational.radar if item.dimension == "0207")
        [section] = report.native_evaluations
        evidence_bytes = artifact.artifact.size_bytes
        evidence_sha256 = artifact.artifact.checksum.value
        review = (
            write_review(store, artifact, strata, output / "review.csv")
            if strata
            else {}
        )
        published = DatasetManifestV2.model_validate_json(manifest.read_bytes())
        population = next(
            item.record_count
            for item in published.artifacts
            if item.kind == "NORMALIZED_DATA"
        )
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "seed": SEED,
            "population": population,
            "run_id": str(evaluation.run_id),
            "dataset_version": str(version),
            "result_id": str(result.calculator_result_id),
            "report_id": str(report.report_id),
            "contract_version": str(contract.contract_version_id),
            "calculator_status": result.status,
            "worker_seconds": seconds,
            "throughput": count / seconds,
            "rss_mib": rss / (1048576 if sys.platform == "darwin" else 1024),
            "evidence_bytes": evidence_bytes,
            "evidence_sha256": evidence_sha256,
            "projected_full_seconds": seconds * population / count,
            "review_strata": review,
            "config_sha256": _sha256_file(CONFIG),
            "statistics": result.statistics,
            "issue_count": section.issue_count,
            "coverage_rule_status": section.evaluation.status,
            "operational_0207_score": axis.deterministic_score,
            "operational_profile_version": operational.scoring_profile_version,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if not args.full and (args.index is None or args.count < 1):
        parser.error("sample mode requires --index and a positive --count")
    args.output.mkdir(parents=True)
    manifest = DatasetManifestV2.model_validate_json(args.manifest.read_bytes())
    artifact = next(
        item for item in manifest.artifacts if item.kind == "NORMALIZED_DATA"
    )
    source, count, strata = args.normalized, artifact.record_count, {}
    if not args.full:
        source, count = args.output / "sample_records.jsonl", args.count
        strata = sample_input(args.normalized, artifact, args.index, source, count)
    summary = run(args.output, source, args.manifest, count, strata, args.full)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Native, streaming 0201 document-format calculator."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

from . import matching, scoring, structure

ARTIFACT = "policy-document-format-decisions.jsonl"
KEY = "policy_document_format"


def settings(parameters):
    defaults = {"threshold": 80, "minimum_coverage": 0.5, "by_type": {}}
    if set(parameters) - defaults.keys():
        raise CalculatorValidationError("unknown document format parameter")
    result = defaults | parameters
    overrides = result["by_type"]
    if not isinstance(overrides, dict) or set(overrides) - matching.SUPPORTED:
        raise CalculatorValidationError("invalid format type overrides")
    for options in [parameters, *overrides.values()]:
        allowed = (
            defaults
            if options is parameters
            else {"threshold": 80, "minimum_coverage": 0.5}
        )
        if not isinstance(options, dict) or set(options) - allowed.keys():
            raise CalculatorValidationError("invalid format scoring parameters")
        for key, upper in (("threshold", 100), ("minimum_coverage", 1)):
            if key in options and (
                type(options[key]) not in (int, float) or not 0 <= options[key] <= upper
            ):
                raise CalculatorValidationError(f"invalid {key}")
    return result


def content(record, role, format_name):
    candidates = [
        item
        for item in record.contents
        if item.role == role
        and item.presence == "PRESENT"
        and isinstance(item.payload, InlineTextPayload)
        and item.payload.format == format_name
        and item.payload.text.strip()
    ]
    return candidates[0] if len(candidates) == 1 else None


def record_input(record):
    title = content(record, "article.title", "plain")
    raw = content(record, "article.body.html", "html")
    body = raw or content(record, "article.body.plain", "plain")
    meta = record.metadata
    sections = {}
    for key in ("extra", "document_number", "publisher", "attachments"):
        value = meta.get(key)
        sections[key] = value if isinstance(value, dict) else {}
    extra, number, publisher, attachments = (
        sections[key]
        for key in ("extra", "document_number", "publisher", "attachments")
    )
    return {
        "title": title.payload.text if title else "",
        "title_id": title.content_id if title else None,
        "content": body.payload.text if body else "",
        "body_id": body.content_id if body else None,
        "is_html": bool(raw),
        "type": extra.get("type"),
        "policyType": extra.get("policyType"),
        "document_type": meta.get("document_type"),
        "filenumber": number.get("filenumber"),
        "issue": number.get("issue"),
        "organization": publisher.get("organization"),
        "publishunit": publisher.get("publishunit"),
        "releaseunit": extra.get("releaseunit"),
        "attachurl": attachments.get("url"),
        "verified_original": bool(
            raw and raw.attributes.get("format_original_verified") is True
        ),
        "verified_standard_scope": bool(
            raw and raw.attributes.get("format_standard_scope_verified") is True
        ),
    }


def analyze(record, options):
    source = record_input(record)
    doc = matching.inspect(source)
    result = scoring.score_document(doc, options)
    locations = []
    segments = []
    for part, score in zip(doc["segments"], result["segments"], strict=True):
        rules = []
        for rule in part["signals"] + part["normative"]:
            refs = []
            # Keep a representative anchor per rule, plus the second paired
            # location for normative checks. Aggregates use the full result.
            for loc in rule["locations"][
                : 2 if rule["classification"] == "NORMATIVE" else 1
            ]:
                field = loc["field"]
                content_id = (
                    loc.get("content_id")
                    or source["title_id" if field == "title" else "body_id"]
                )
                if content_id and 0 <= loc["start"] < loc["end"] <= len(source[field]):
                    refs.append(len(locations))
                    locations.append(
                        {
                            "content_id": content_id,
                            "coordinate_space": loc["coordinate_space"],
                            "start": loc["start"],
                            "end": loc["end"],
                            "attributes": {
                                "rule_id": rule["id"],
                                "segment_id": part["id"],
                            },
                        }
                    )
            rules.append(
                {k: v for k, v in rule.items() if k != "locations"}
                | {"location_indices": refs}
            )
        segments.append(
            {
                **score,
                "id": part["id"],
                "parent": part["parent"],
                "required": part["required"],
                "doc_type": part["type_candidate"],
                "metadata_candidates": part["metadata_candidates"],
                "competing_types": part["competing_types"],
                "rules": rules,
                "location": part["location"],
                "structure_types": part["structure_types"],
            }
        )
    violation = next(
        (
            r["id"]
            for part in segments
            for r in part["rules"]
            if r["classification"] == "NORMATIVE"
            and r["status"] == "FAIL"
            and r["location_indices"]
        ),
        None,
    )
    return CalculatorDecisionV1(
        row_id=record.sample_id,
        sample_id=record.sample_id,
        status=str(result["label"]),
        rule_id=violation or "DOCUMENT_FORMAT",
        target="SELF",
        reason="|".join(result["reasons"]) or "WEIGHTED_TEMPLATE_DECISION",
        locations=locations,
        evidence_refs=[{"artifact_key": ARTIFACT, "row_id": record.sample_id}],
        attributes={
            "label": result["label"],
            "score": result["score"],
            "template_version": matching.library()["version"],
            "style_score": result["style_score"],
            "segments": segments,
            "doc_type": doc["type_candidate"],
            "reasons": result["reasons"],
            "attachment_status": doc.get("attachment_status"),
            "normative_violation": violation,
        },
    )


def statistics(counts, types, reasons, signals):
    total, passed, failed = (counts[k] for k in ("input", "1", "0"))
    evaluable = passed + failed
    violations = counts["violations"]
    return {
        "input_count": total,
        "compliant_record_count": passed,
        "non_compliant_record_count": failed,
        "not_evaluated_count": counts["NOT_EVALUATED"],
        "evaluable_record_count": evaluable,
        "normative_violation_count": violations,
        "evaluable_rate": evaluable / total if total else None,
        "evaluated_compliance_ratio": passed / evaluable if evaluable else None,
        "compliant_record_ratio": passed / total if total else None,
        "normative_violation_rate": violations / total if total else None,
        "document_types": dict(types),
        "unknown_reasons": dict(reasons),
        "rule_outcomes": dict(signals),
    }


class PolicyDocumentFormatCalculator:
    calculator_key = KEY
    version = "1.0.0"

    def source_paths(self):
        return (
            Path(__file__),
            Path(matching.__file__),
            Path(scoring.__file__),
            Path(structure.__file__),
            matching.TEMPLATE_PATH,
        )

    def validate(self, context: CalculatorContext, parameters):
        settings(parameters)
        return []

    def execute(self, context: CalculatorContext, parameters):
        options = settings(parameters)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / ARTIFACT
        counts, types, reasons, signals = Counter(), Counter(), Counter(), Counter()
        with (
            context.input_path.open("rb") as source,
            destination.open("w", encoding="utf-8") as output,
            closing(
                sqlite3.connect(context.output_dir / "format-seen.sqlite3")
            ) as seen,
        ):
            seen.execute(
                "CREATE TABLE seen (sample_id TEXT PRIMARY KEY, digest BLOB NOT NULL)"
            )
            for line in source:
                if not line.strip():
                    continue
                record = SampleRecordV2.model_validate_json(line)
                if record.dataset_version_id != context.dataset_version_id:
                    raise CalculatorValidationError(
                        "normalized sample belongs to another dataset version"
                    )
                digest = hashlib.sha256(line.strip()).digest()
                if not seen.execute(
                    "INSERT OR IGNORE INTO seen VALUES (?, ?)",
                    (record.sample_id, digest),
                ).rowcount:
                    if (
                        seen.execute(
                            "SELECT digest FROM seen WHERE sample_id = ?",
                            (record.sample_id,),
                        ).fetchone()[0]
                        != digest
                    ):
                        raise CalculatorValidationError(
                            "conflicting duplicate sample ID"
                        )
                    continue
                decision = analyze(record, options)
                attributes = decision.attributes
                counts["input"] += 1
                counts[decision.status] += 1
                counts["violations"] += bool(attributes["normative_violation"])
                types[attributes["doc_type"] or "UNKNOWN"] += 1
                reasons.update(attributes["reasons"])
                for part in attributes["segments"]:
                    for rule in part["rules"]:
                        signals[f"{rule['id']}/{rule['status']}"] += 1
                        if rule["status"] == "UNKNOWN":
                            reasons[f"{rule['id']}/{rule['reason']}"] += 1
                output.write(decision.model_dump_json() + "\n")
                if context.progress and counts["input"] % 1000 == 0:
                    context.progress(counts["input"], {})
        return NativeCalculatorOutput(
            statistics(counts, types, reasons, signals),
            status="OK" if counts["input"] else "NOT_APPLICABLE",
            status_detail={"thresholds_pending_calibration": True, "settings": options},
            decisions=EvidenceOutput(
                destination,
                "POLICY_DOCUMENT_FORMAT_DECISIONS",
                DECISION_SCHEMA_REF,
                counts["input"],
                redaction_policy="identifiers-offsets-rule-states-no-excerpts",
            ),
        )

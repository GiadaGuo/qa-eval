from __future__ import annotations

import hashlib
import html
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
    DecisionEvidenceLocatorV1,
    ObservationLocationV1,
)
from qa_eval.contracts.datasets import (
    ContentItem,
    DatasetManifestV2,
    InlineTextPayload,
    SampleRecordV2,
)

from . import rules

SHANGHAI = ZoneInfo("Asia/Shanghai")
DECISION_ARTIFACT = "policy-effectiveness-decisions.jsonl"
TITLE = "article.title"
PLAIN = "article.body.plain"
HTML = "article.body.html"
HTML_COORDINATES = "POLICY_PHASE1_HTML_STRIPPED_UNICODE_CODE_POINT_V1"
TEXT_COORDINATES = "CONTENT_TEXT_UNICODE_CODE_POINT"
TAG = re.compile(r"<[^>]+>")
NON_VISIBLE = re.compile(
    r"<!--.*?(?:-->|\Z)|<(script|style)\b[^>]*>.*?(?:</\1\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def body_text(content: ContentItem) -> tuple[str, str]:
    text = content.payload.text
    if content.payload.format == "html":
        # Offsets refer to this transformed view. Separate hidden regions from
        # nearby text so that they cannot form part of a rule match.
        return html.unescape(TAG.sub("", NON_VISIBLE.sub("\n", text))), HTML_COORDINATES
    return text, TEXT_COORDINATES


def _content(
    record: SampleRecordV2, role: str, expected_format: str
) -> ContentItem | None:
    candidates = [
        item
        for item in record.contents
        if item.role == role
        and item.presence == "PRESENT"
        and isinstance(item.payload, InlineTextPayload)
        and item.payload.format == expected_format
        and item.payload.text.strip()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _temporal_date(metadata: JsonObject, key: str) -> date | None:
    temporal = metadata.get("temporal")
    field = temporal.get(key) if isinstance(temporal, dict) else None
    value = field.get("value") if isinstance(field, dict) else None
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    day = when.astimezone(SHANGHAI).date() if when.tzinfo is not None else when.date()
    return None if day.year == 2099 else day


def _document_type_source(manifest: DatasetManifestV2) -> str | None:
    mapping = manifest.field_mapping
    if any(
        item.target
        and item.target.kind == "METADATA"
        and item.target.name == "document_type"
        for item in mapping.mappings
    ):
        return "document_type"
    # The audited release explicitly preserves this field through the 2.0 adapter.
    if (mapping.adapter.key, mapping.adapter.version) == (
        "jhsjk_policy_article",
        "2.0.0",
    ) and any(
        item.source_path == "$.type"
        and item.action == "PASSTHROUGH"
        and item.value_type == "string"
        for item in mapping.mappings
    ):
        return "extra.type"
    return None


def _document_type(metadata: JsonObject, source: str | None) -> str | None:
    if source == "document_type":
        raw = metadata.get("document_type")
    elif source == "extra.type" and isinstance(metadata.get("extra"), dict):
        raw = metadata["extra"].get("type")
    else:
        return None
    return (
        raw.strip()
        if isinstance(raw, str) and raw.strip() not in {"", "None"}
        else None
    )


def _read_date(parameters: JsonObject, key: str, default: date) -> date:
    raw = parameters.get(key)
    if raw is None:
        return default
    try:
        day = date.fromisoformat(raw)
        if day.isoformat() == raw:
            return day
    except (TypeError, ValueError):
        pass
    raise CalculatorValidationError(f"{key} must be an ISO date")


def _event(
    record: SampleRecordV2, reference: date, observed: date, type_source: str | None
):
    title = _content(record, TITLE, "plain")
    body = _content(record, HTML, "html") or _content(record, PLAIN, "plain")
    document_type = _document_type(record.metadata, type_source)
    publication = _temporal_date(record.metadata, "formatted_at") or _temporal_date(
        record.metadata, "published_at"
    )
    applicability, state, reason, start, end = (
        "APPLICABILITY_UNCERTAIN",
        None,
        "MISSING_OR_AMBIGUOUS_TITLE_OR_BODY",
        None,
        None,
    )
    signals = []
    locations = []
    if title and body:
        text, coordinate_space = body_text(body)
        if text.strip():
            applicability, state, reason, start, end, signals = rules.evaluate(
                title.payload.text,
                text,
                document_type,
                publication,
                reference,
                observed,
            )
            hashes = {
                "title": hashlib.sha256(title.payload.text.encode()).hexdigest(),
                "content": hashlib.sha256(text.encode()).hexdigest(),
            }
            for signal in signals:
                in_title = signal["source_field"] == "title"
                locations.append(
                    ObservationLocationV1(
                        content_id=title.content_id if in_title else body.content_id,
                        coordinate_space=TEXT_COORDINATES
                        if in_title
                        else coordinate_space,
                        start=signal["start"],
                        end=signal["end"],
                        attributes={
                            "rule_id": signal["rule_id"],
                            "target": signal["target_scope"],
                            "signal": signal["signal_type"],
                            "date": signal["date"],
                            "text_fingerprint": f"sha256:{hashes[signal['source_field']]}",
                        },
                    )
                )
    rule_ids = sorted({signal["rule_id"] for signal in signals if signal["rule_id"]})
    targets = {signal["target_scope"] for signal in signals if signal["rule_id"]}
    return CalculatorDecisionV1(
        row_id=record.sample_id,
        sample_id=record.sample_id,
        rule_id=rule_ids[0] if rule_ids else "APPLICABILITY",
        status=state or "NOT_EVALUATED",
        target=next(iter(targets)) if len(targets) == 1 else "UNCERTAIN",
        reason=reason,
        locations=locations,
        evidence_refs=[
            DecisionEvidenceLocatorV1(
                artifact_key=DECISION_ARTIFACT, row_id=record.sample_id
            )
        ]
        if locations
        else [],
        attributes={
            "applicability": applicability,
            "effectiveness": state,
            "rule_ids": rule_ids,
            "document_type_available": document_type is not None,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "publication_date": publication.isoformat() if publication else None,
            "source_record_id": record.source.source_record_id,
            "source_row": record.source.source_row,
            "source_artifact_id": str(record.source.source_artifact_id),
        },
    )


def _statistics(counts: Counter) -> JsonObject:
    applicable = counts["APPLICABLE"]
    expired = counts["EXPIRED_EVIDENCED"]
    within = counts["NOT_EXPIRED_BY_KNOWN_TERM"]
    decidable = expired + within
    return {
        "input_count": counts["input"],
        "applicable_count": applicable,
        "not_applicable_count": counts["NOT_APPLICABLE"],
        "applicability_unknown_count": counts["APPLICABILITY_UNCERTAIN"],
        "expired_evidenced_count": expired,
        "not_expired_by_known_term_count": within,
        "undetermined_count": counts["UNDETERMINED"],
        "decidable_count": decidable,
        "decidable_coverage": decidable / applicable if applicable else None,
        "expired_evidence_rate": expired / applicable if applicable else None,
    }


class PolicyEffectivenessCalculator:
    calculator_key = "policy_effectiveness"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return Path(__file__), Path(rules.__file__)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        self._settings(context, parameters)
        return []

    @staticmethod
    def _settings(context, parameters):
        path = context.input_artifacts.get("DATASET_MANIFEST")
        if path is None:
            raise CalculatorValidationError(
                "policy effectiveness requires the published manifest"
            )
        manifest = DatasetManifestV2.model_validate_json(path.read_bytes())
        if manifest.dataset_version_id != context.dataset_version_id:
            raise CalculatorValidationError(
                "manifest belongs to a different dataset version"
            )
        local_run_day = (
            (context.run_created_at or datetime.now(SHANGHAI))
            .astimezone(SHANGHAI)
            .date()
        )
        captured = manifest.source_snapshot.captured_at.astimezone(SHANGHAI).date()
        observed = _read_date(parameters, "observation_date", captured)
        reference = _read_date(parameters, "reference_date", local_run_day)
        return reference, observed, _document_type_source(manifest)

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> NativeCalculatorOutput:
        reference, observed, type_source = self._settings(context, parameters)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISION_ARTIFACT
        counts = Counter()
        duplicates = 0
        with (
            context.input_path.open("rb") as source,
            destination.open("w", encoding="utf-8") as output,
            closing(sqlite3.connect(context.output_dir / "seen.sqlite3")) as seen,
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
                inserted = seen.execute(
                    "INSERT OR IGNORE INTO seen VALUES (?, ?)",
                    (record.sample_id, digest),
                )
                if not inserted.rowcount:
                    previous = seen.execute(
                        "SELECT digest FROM seen WHERE sample_id = ?",
                        (record.sample_id,),
                    ).fetchone()
                    if previous[0] != digest:
                        raise CalculatorValidationError(
                            "conflicting duplicate sample ID"
                        )
                    duplicates += 1
                    continue
                event = _event(record, reference, observed, type_source)
                counts["input"] += 1
                counts[event.attributes["applicability"]] += 1
                if event.attributes["effectiveness"] is not None:
                    counts[event.status] += 1
                output.write(event.model_dump_json() + "\n")
                if context.progress and counts["input"] % 1000 == 0:
                    context.progress(counts["input"], {})
        detail = {
            "reference_date": reference.isoformat(),
            "observation_date": observed.isoformat(),
            "observation_date_source": "EXPLICIT_REVIEW"
            if "observation_date" in parameters
            else "MANIFEST_CAPTURE",
            "document_type_source": type_source,
            "duplicate_input_rows": duplicates,
        }
        return NativeCalculatorOutput(
            statistics=_statistics(counts),
            status="OK" if counts["APPLICABLE"] else "NOT_APPLICABLE",
            status_detail=detail,
            decisions=EvidenceOutput(
                destination,
                "POLICY_EFFECTIVENESS_DECISIONS",
                DECISION_SCHEMA_REF,
                counts["input"],
                attributes={
                    "offset_unit": "UNICODE_CODE_POINT",
                    "interval": "ZERO_BASED_HALF_OPEN",
                },
            ),
        )

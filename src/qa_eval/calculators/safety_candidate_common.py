"""Shared, privacy-safe mechanics for deterministic 0202 candidate detectors."""

from __future__ import annotations

import hashlib
import html.parser
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import ContentItem, InlineTextPayload, SampleRecordV2

EVIDENCE_SCHEMA = "qa-eval://schemas/evidence/sensitive-entity-hits/1.1.0"


@dataclass(frozen=True)
class LogicalText:
    record_id: str
    content_id: str
    field_role: str
    text: str
    text_format: str
    field_path: str
    coordinate_space: str
    mapping_precision: str


@dataclass(frozen=True)
class CandidateHit:
    category: str
    start: int
    end: int
    masked_excerpt: str
    rule_ids: tuple[str, ...]
    metadata: JsonObject


class Detector(Protocol):
    detector_id: str
    detector_version: str
    config_fingerprint: str

    def hits(self, text: str) -> Iterator[CandidateHit]: ...

    def diagnostics(self) -> JsonObject: ...


class _VisibleTextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def extract(self, value: str) -> str:
        self.reset()
        self.parts.clear()
        self.hidden_depth = 0
        try:
            self.feed(value)
            self.close()
        except (AssertionError, ValueError):
            return value
        return "".join(self.parts)


def sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def optional_string_set(parameters: JsonObject, key: str) -> frozenset[str] | None:
    value = parameters.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise CalculatorValidationError(
            f"{key} must be a non-empty list of unique non-empty strings"
        )
    return frozenset(value)


def validate_common(context: CalculatorContext, parameters: JsonObject) -> None:
    if context.input_schema_ref != "2.0":
        raise CalculatorValidationError(
            "Calculator requires SampleRecord 2.0 normalized input",
            input_schema_ref=context.input_schema_ref,
        )
    if not context.input_path.is_file():
        raise CalculatorValidationError("normalized input artifact is unavailable")
    optional_string_set(parameters, "include_roles")
    optional_string_set(parameters, "exclude_roles")
    optional_string_set(parameters, "include_content_ids")


def _eligible(
    item: ContentItem,
    include_roles: frozenset[str] | None,
    exclude_roles: frozenset[str],
    include_ids: frozenset[str] | None,
) -> str | None:
    if item.modality != "text":
        return "NON_TEXT_MODALITY"
    if include_roles is not None and item.role not in include_roles:
        return "ROLE_NOT_INCLUDED"
    if item.role in exclude_roles:
        return "ROLE_EXCLUDED"
    if include_ids is not None and item.content_id not in include_ids:
        return "CONTENT_ID_NOT_INCLUDED"
    if item.presence != "PRESENT":
        return f"PRESENCE_{item.presence}"
    if not isinstance(item.payload, InlineTextPayload):
        return "NOT_INLINE_TEXT"
    if not item.payload.text:
        return "EMPTY_TEXT"
    return None


def _row(logical: LogicalText, hit: CandidateHit, detector: Detector) -> JsonObject:
    identity = {
        "detector_id": detector.detector_id,
        "detector_version": detector.detector_version,
        "config_fingerprint": detector.config_fingerprint,
        "sample_id": logical.record_id,
        "content_id": logical.content_id,
        "category": hit.category,
        "start": hit.start,
        "end": hit.end,
        "coordinate_space": logical.coordinate_space,
        "rule_ids": list(hit.rule_ids),
    }
    return {
        "row_id": stable_digest(identity),
        "sample_id": logical.record_id,
        "content_id": logical.content_id,
        "field_path": logical.field_path,
        "logical_field": logical.field_role,
        "category": hit.category,
        "start": hit.start,
        "end": hit.end,
        "offset_unit": "UNICODE_CODE_POINT",
        "coordinate_space": logical.coordinate_space,
        "text_version": sha256_text(logical.text),
        "mapping_precision": logical.mapping_precision,
        "channels": ["RULE"],
        "masked_excerpt": hit.masked_excerpt,
        "model_id": None,
        "model_score": None,
        "model_original_label": None,
        "rule_ids": list(hit.rule_ids),
        "review_status": "UNREVIEWED",
    }


def execute_detector(
    context: CalculatorContext,
    parameters: JsonObject,
    detector: Detector,
    evidence_filename: str,
    covered: list[str],
    limitations: list[str],
) -> CalculatorOutput:
    validate_common(context, parameters)
    include_roles = optional_string_set(parameters, "include_roles")
    exclude_roles = optional_string_set(parameters, "exclude_roles") or frozenset()
    include_ids = optional_string_set(parameters, "include_content_ids")
    parser = _VisibleTextParser()
    input_count = evaluated = affected = scanned_chars = raw_matches = 0
    selected_contents = evidence_rows = 0
    exclusions: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    context.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = context.output_dir / evidence_filename

    with (
        context.input_path.open(encoding="utf-8") as source,
        evidence_path.open("w", encoding="utf-8") as evidence,
    ):
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                continue
            input_count += 1
            try:
                sample = SampleRecordV2.model_validate_json(raw_line)
            except ValidationError as exc:
                raise CalculatorValidationError(
                    "normalized input contains an invalid SampleRecord",
                    line_number=line_number,
                ) from exc
            record_eligible = False
            record_affected = False
            for item in sample.contents:
                reason = _eligible(item, include_roles, exclude_roles, include_ids)
                if reason:
                    exclusions[reason] += 1
                    continue
                assert isinstance(item.payload, InlineTextPayload)
                record_eligible = True
                selected_contents += 1
                raw_text = item.payload.text
                is_html = item.payload.format == "html"
                text = parser.extract(raw_text) if is_html else raw_text
                scanned_chars += len(text)
                logical = LogicalText(
                    record_id=sample.sample_id,
                    content_id=item.content_id,
                    field_role=item.role,
                    text=text,
                    text_format=item.payload.format,
                    field_path=f"$.contents[content_id={item.content_id}].payload.text",
                    coordinate_space=(
                        "VISIBLE_TEXT_UNICODE_CODE_POINT"
                        if is_html
                        else "CONTENT_TEXT_UNICODE_CODE_POINT"
                    ),
                    mapping_precision="EXACT_VISIBLE_TEXT" if is_html else "EXACT",
                )
                unique: dict[tuple[str, int, int, tuple[str, ...]], CandidateHit] = {}
                for hit in detector.hits(text):
                    raw_matches += 1
                    unique.setdefault(
                        (hit.category, hit.start, hit.end, hit.rule_ids), hit
                    )
                for hit in sorted(
                    unique.values(),
                    key=lambda value: (value.start, value.end, value.category),
                ):
                    evidence.write(
                        json.dumps(
                            _row(logical, hit, detector),
                            ensure_ascii=True,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    evidence_rows += 1
                    categories[hit.category] += 1
                    record_affected = True
            if record_eligible:
                evaluated += 1
            if record_affected:
                affected += 1

    population = {
        "input_count": input_count,
        "evaluated_count": evaluated,
        "excluded_count": input_count - evaluated,
        "exclusions": (
            [{"reason": "NO_ELIGIBLE_TEXT", "count": input_count - evaluated}]
            if input_count != evaluated
            else []
        ),
    }
    semantic_coverage = {
        "level": "PARTIAL",
        "covered": covered,
        "excluded": limitations,
        "limitations": limitations,
    }
    if input_count == 0:
        return CalculatorOutput(
            observations=[],
            population=population,
            semantic_coverage=semantic_coverage,
            status="MISSING",
            status_detail={"reason_code": "NO_INPUT_RECORDS"},
        )
    if evaluated == 0:
        return CalculatorOutput(
            observations=[],
            population=population,
            semantic_coverage=semantic_coverage,
            status="NOT_APPLICABLE",
            status_detail={"reason_code": "NO_ELIGIBLE_TEXT"},
        )
    observations: list[JsonObject] = [
        {
            "key": "sensitive_entity_hit_count",
            "value": evidence_rows,
            "value_type": "NUMBER",
            "unit": "entity",
        },
        {
            "key": "sensitive_entity_type_counts",
            "value": dict(sorted(categories.items())),
            "value_type": "OBJECT",
            "unit": "entity",
        },
        {
            "key": "affected_record_count",
            "value": affected,
            "value_type": "NUMBER",
            "unit": "record",
        },
        {
            "key": "evaluated_record_count",
            "value": evaluated,
            "value_type": "NUMBER",
            "unit": "record",
        },
        {
            "key": "scan_coverage_ratio",
            "value": 1.0,
            "value_type": "NUMBER",
            "unit": "ratio",
            "numerator": scanned_chars,
            "denominator": scanned_chars,
        },
    ]
    return CalculatorOutput(
        observations=observations,
        population=population,
        semantic_coverage=semantic_coverage,
        evidence=[
            EvidenceOutput(
                path=evidence_path,
                kind="SENSITIVE_ENTITY_HITS",
                schema_ref=EVIDENCE_SCHEMA,
                record_count=evidence_rows,
                subject_scope="CONTENT",
                contains_raw_content=False,
                redaction_policy="category-mask-no-raw-sensitive-value",
                attributes={
                    "detected_count": evidence_rows,
                    "emitted_count": evidence_rows,
                    "truncated": False,
                    "truncation_reason": None,
                    "completeness_status": "COMPLETE",
                    "counting_unit": "UNIQUE_CANDIDATE_HIT",
                    "one_record_per_detected_fact": True,
                    "row_identity_version": "1.0",
                    "location_schema_version": "1.0",
                    "detector_id": detector.detector_id,
                    "detector_version": detector.detector_version,
                    "config_fingerprint": detector.config_fingerprint,
                },
            )
        ],
        diagnostics=[
            {
                "key": "candidate_detection_statistics",
                "raw_match_count": raw_matches,
                "unique_match_count": evidence_rows,
                "affected_record_count": affected,
                "evaluated_record_count": evaluated,
                "excluded_record_count": input_count - evaluated,
                "selected_content_count": selected_contents,
                "scanned_character_count": scanned_chars,
                "counts_by_category": dict(sorted(categories.items())),
                "skipped_content_counts_by_reason": dict(sorted(exclusions.items())),
                "deduplication_key": "record_id, content_id, category, start, end",
                **detector.diagnostics(),
            }
        ],
    )

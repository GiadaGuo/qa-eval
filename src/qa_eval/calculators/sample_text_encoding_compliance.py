"""BG-02-02 encoding-representation checks over SampleRecord 2.0 text."""

from __future__ import annotations

import html.parser
import json
import unicodedata
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_purity_rules import (
    RULESET_VERSION,
    iter_mojibake_matches,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import ContentItem, InlineTextPayload, SampleRecordV2

_CONTEXT_RADIUS = 20
_ALLOWED_CONTROLS = frozenset("\t\n\r")


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

    def visible_text(self, value: str) -> str:
        self.reset()
        self.parts.clear()
        self.hidden_depth = 0
        try:
            self.feed(value)
            self.close()
        except AssertionError:
            # HTMLParser is deliberately tolerant; this remains a defensive
            # fallback and does not turn malformed markup into an encoding hit.
            return value
        return "".join(self.parts)


def _optional_string_set(parameters: JsonObject, key: str) -> frozenset[str] | None:
    value = parameters.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise CalculatorValidationError(
            f"{key} must be a non-empty list of non-empty strings"
        )
    if len(value) != len(set(value)):
        raise CalculatorValidationError(f"{key} must contain unique strings")
    return frozenset(value)


def _boolean(parameters: JsonObject, key: str, default: bool) -> bool:
    value = parameters.get(key, default)
    if not isinstance(value, bool):
        raise CalculatorValidationError(f"{key} must be a boolean")
    return value


def _is_noncharacter(char: str) -> bool:
    codepoint = ord(char)
    return 0xFDD0 <= codepoint <= 0xFDEF or (codepoint & 0xFFFF) in {
        0xFFFE,
        0xFFFF,
    }


class SampleTextEncodingComplianceCalculator:
    calculator_key = "sample_text_encoding_compliance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "text_purity_rules.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "sample_text_encoding_compliance requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        if not context.input_path.is_file():
            raise CalculatorValidationError(
                "normalized input artifact is unavailable",
                input_path=str(context.input_path),
            )
        _optional_string_set(parameters, "include_roles")
        _optional_string_set(parameters, "exclude_roles")
        _optional_string_set(parameters, "include_content_ids")
        for key in (
            "check_replacement_character",
            "check_unicode_validity",
            "check_mojibake",
            "check_control_characters",
        ):
            _boolean(parameters, key, True)
        maximum = parameters.get("max_evidence_per_record", 20)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            raise CalculatorValidationError(
                "max_evidence_per_record must be an integer of at least 1"
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        include_roles = _optional_string_set(parameters, "include_roles")
        exclude_roles = _optional_string_set(parameters, "exclude_roles") or frozenset()
        include_ids = _optional_string_set(parameters, "include_content_ids")
        checks = {
            "replacement": _boolean(parameters, "check_replacement_character", True),
            "unicode": _boolean(parameters, "check_unicode_validity", True),
            "mojibake": _boolean(parameters, "check_mojibake", True),
            "controls": _boolean(parameters, "check_control_characters", True),
        }
        evidence_limit = int(parameters.get("max_evidence_per_record", 20))

        input_count = eligible_records = scanned_contents = 0
        affected_records = affected_contents = violation_count = 0
        skipped = Counter()
        code_counts: Counter[str] = Counter()
        parser = _VisibleTextParser()
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "sample-text-encoding-violations.jsonl"
        evidence_rows = 0

        with (
            context.input_path.open(encoding="utf-8") as input_file,
            evidence_path.open("w", encoding="utf-8") as evidence_file,
        ):
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                input_count += 1
                try:
                    # Python's JSON decoder preserves escaped isolated
                    # surrogates so this calculator can report them. The
                    # pydantic-core JSON decoder rejects them before model
                    # validation, which would make the target rule unreachable.
                    sample = SampleRecordV2.model_validate(json.loads(raw_line))
                except (json.JSONDecodeError, ValidationError) as exc:
                    raise CalculatorValidationError(
                        "normalized input contains an invalid SampleRecord",
                        line_number=line_number,
                    ) from exc

                record_eligible = False
                record_affected = False
                emitted_for_record = 0
                for item in sample.contents:
                    reason = self._skip_reason(
                        item, include_roles, exclude_roles, include_ids
                    )
                    if reason is not None:
                        skipped[reason] += 1
                        continue
                    assert isinstance(item.payload, InlineTextPayload)
                    text = item.payload.text
                    if not text:
                        skipped["EMPTY_TEXT"] += 1
                        continue
                    record_eligible = True
                    scanned_contents += 1
                    visible = (
                        parser.visible_text(text)
                        if item.payload.format == "html"
                        else text
                    )
                    violations = list(self._violations(text, visible, checks))
                    if not violations:
                        continue
                    record_affected = True
                    affected_contents += 1
                    violation_count += len(violations)
                    code_counts.update(hit["violation_code"] for hit in violations)
                    for hit in violations:
                        if emitted_for_record >= evidence_limit:
                            break
                        row = {
                            "sample_id": sample.sample_id,
                            "content_id": item.content_id,
                            "role": item.role,
                            "source_paths": item.attributes.get("source_paths", []),
                            **hit,
                        }
                        evidence_file.write(
                            json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                        )
                        evidence_rows += 1
                        emitted_for_record += 1
                if record_eligible:
                    eligible_records += 1
                if record_affected:
                    affected_records += 1

        compliant = eligible_records - affected_records
        observations: list[JsonObject] = [
            {
                "key": "compliant_record_count",
                "value": compliant,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "non_compliant_record_count",
                "value": affected_records,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "evaluable_record_count",
                "value": eligible_records,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ]
        status = "OK"
        status_detail: JsonObject = {}
        if eligible_records:
            observations.insert(
                0,
                {
                    "key": "compliant_record_ratio",
                    "value": compliant / eligible_records,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": compliant,
                    "denominator": eligible_records,
                },
            )
        else:
            status = "NOT_APPLICABLE"
            observations = []
            status_detail = {
                "reason_code": "NO_ELIGIBLE_INLINE_TEXT",
                "message": "No PRESENT inline-text ContentItem matched the configured selectors",
            }

        return CalculatorOutput(
            status=status,
            status_detail=status_detail,
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": eligible_records,
                "excluded_count": input_count - eligible_records,
                "exclusions": (
                    [
                        {
                            "reason": "NO_ELIGIBLE_INLINE_TEXT",
                            "count": input_count - eligible_records,
                        }
                    ]
                    if input_count != eligible_records
                    else []
                ),
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "SampleRecord text-field Unicode anomalies",
                    "Unicode replacement characters",
                    "shared known mojibake signatures",
                    "unexpected control characters",
                ],
                "excluded": [
                    "UTF-8 validity and byte offsets in original uploaded files",
                    "physical PDF/DOCX/OFD encoding",
                    "OCR accuracy and semantic text errors",
                ],
                "limitations": [
                    "U+FFFD is evidence of a suspected upstream decoding problem, not proof of an original invalid byte",
                    "HTML mojibake offsets refer to extracted visible text and are not raw HTML offsets",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="SAMPLE_TEXT_ENCODING_VIOLATIONS",
                    schema_ref="qa-eval://schemas/evidence/bg-02-02-text-encoding/1.0.0",
                    record_count=evidence_rows,
                    redaction_policy="matched-excerpts-only",
                    attributes={"shared_mojibake_ruleset_version": RULESET_VERSION},
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "sample_text_encoding_statistics",
                    "eligible_record_count": eligible_records,
                    "scanned_record_count": eligible_records,
                    "scanned_content_count": scanned_contents,
                    "affected_record_count": affected_records,
                    "affected_content_count": affected_contents,
                    "violation_count": violation_count,
                    "violation_counts_by_code": dict(sorted(code_counts.items())),
                    "skipped_content_count": sum(skipped.values()),
                    "skipped_content_counts_by_reason": dict(sorted(skipped.items())),
                    "emitted_evidence_count": evidence_rows,
                    "counting_semantics": {
                        "record": "a sample contributes at most once regardless of affected ContentItem count",
                        "content": "each eligible affected ContentItem contributes once",
                        "violation": "each detected occurrence contributes once; derived contents remain distinct evidence",
                        "source": "source_paths are retained for traceability but uncertain HTML/plain positions are not merged",
                    },
                },
            ],
        )

    @staticmethod
    def _skip_reason(
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
        return None

    @staticmethod
    def _violations(
        raw_text: str, visible_text: str, checks: dict[str, bool]
    ) -> Iterator[dict[str, Any]]:
        if checks["unicode"]:
            for index, char in enumerate(raw_text):
                codepoint = ord(char)
                if 0xD800 <= codepoint <= 0xDFFF:
                    yield _hit(
                        raw_text,
                        index,
                        index + 1,
                        "INVALID_UNICODE_SURROGATE",
                        "ERROR",
                        "raw_text",
                        "Detected an isolated UTF-16 surrogate code point",
                    )
                elif _is_noncharacter(char):
                    yield _hit(
                        raw_text,
                        index,
                        index + 1,
                        "INVALID_UNICODE_CODE_POINT",
                        "ERROR",
                        "raw_text",
                        "Detected a Unicode noncharacter code point",
                    )
        if checks["replacement"]:
            for index, char in enumerate(raw_text):
                if char == "\ufffd":
                    yield _hit(
                        raw_text,
                        index,
                        index + 1,
                        "REPLACEMENT_CHARACTER",
                        "WARNING",
                        "raw_text",
                        "Detected a Unicode replacement character; suspected upstream decoding anomaly",
                    )
        if checks["controls"]:
            for index, char in enumerate(raw_text):
                if unicodedata.category(char) == "Cc" and char not in _ALLOWED_CONTROLS:
                    yield _hit(
                        raw_text,
                        index,
                        index + 1,
                        "SUSPICIOUS_CONTROL_CHARACTER",
                        "WARNING",
                        "raw_text",
                        "Detected an unexpected control character",
                    )
        if checks["mojibake"]:
            for match in iter_mojibake_matches(visible_text):
                yield _hit(
                    visible_text,
                    match.start(),
                    match.end(),
                    "SUSPICIOUS_MOJIBAKE",
                    "WARNING",
                    "visible_text" if visible_text is not raw_text else "raw_text",
                    "Detected a known mojibake signature; manual review is recommended",
                )


def _hit(
    text: str,
    start: int,
    end: int,
    code: str,
    severity: str,
    coordinate_space: str,
    message: str,
) -> dict[str, Any]:
    return {
        "violation_code": code,
        "severity": severity,
        "start_offset": start,
        "end_offset": end,
        "offset_semantics": "zero-based Python Unicode character index, left-closed/right-open",
        "coordinate_space": coordinate_space,
        "matched_text": text[start:end],
        "context": text[
            max(0, start - _CONTEXT_RADIUS) : min(len(text), end + _CONTEXT_RADIUS)
        ],
        "message": message,
    }


__all__ = ["SampleTextEncodingComplianceCalculator"]

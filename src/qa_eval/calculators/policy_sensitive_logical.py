"""Run the frozen 0202 detectors against selected policy text fields."""

import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from qa_eval.calculators import policy_resolved_fields
from qa_eval.calculators.policy_resolved_fields import (
    TEXT_FIELDS,
    field,
    require_resolver,
)
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    EvidenceOutput,
)
from qa_eval.calculators.safety_candidate_common import (
    EVIDENCE_SCHEMA,
    CandidateHit,
    Detector,
    LogicalText,
    _row,
    _VisibleTextParser,
    optional_string_set,
)
from qa_eval.calculators.sensitive_dictionary_match import (
    DictionaryDetector,
    SensitiveDictionaryMatchCalculator,
    _load_terms,
)
from qa_eval.calculators.structured_sensitive_elements import (
    CATEGORIES,
    StructuredDetector,
    StructuredSensitiveElementCalculator,
)
from qa_eval.contracts.base import JsonObject


def execute_resolved_detector(
    context: CalculatorContext,
    parameters: JsonObject,
    detector: Detector,
    evidence_filename: str,
    covered: list[str],
    limitations: list[str],
) -> CalculatorOutput:
    require_resolver(context)
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

    with evidence_path.open("w", encoding="utf-8") as evidence:
        for sample in context.resolved_records():
            input_count += 1
            record_eligible = False
            record_affected = False
            for key, (role, format_name) in TEXT_FIELDS.items():
                resolved = sample.values[key]
                if resolved.status == "UNBOUND":
                    continue
                if include_roles is not None and role not in include_roles:
                    reason = "ROLE_NOT_INCLUDED"
                elif role in exclude_roles:
                    reason = "ROLE_EXCLUDED"
                elif include_ids is not None and key not in include_ids:
                    reason = "CONTENT_ID_NOT_INCLUDED"
                else:
                    raw_text = field(sample, key)
                    reason = {
                        "MISSING": "PRESENCE_MISSING",
                        "NULL": "PRESENCE_NULL",
                        "EMPTY": "EMPTY_TEXT",
                    }.get(resolved.status)
                if reason:
                    exclusions[reason] += 1
                    continue
                assert resolved.origin is not None
                record_eligible = True
                selected_contents += 1
                is_html = format_name == "html"
                text = parser.extract(raw_text) if is_html else raw_text
                scanned_chars += len(text)
                pointer = "/" + "/".join(
                    token.replace("~", "~0").replace("/", "~1")
                    for token in resolved.origin.field_tokens
                )
                logical = LogicalText(
                    record_id=str(sample.sample_id),
                    content_id=key,
                    field_role=role,
                    text=text,
                    text_format=format_name,
                    field_path=pointer,
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
                            {
                                **_row(logical, hit, detector),
                                "source_origin": asdict(resolved.origin),
                            },
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


class LogicalSensitiveDictionaryMatchCalculator(SensitiveDictionaryMatchCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(policy_resolved_fields.__file__),
        )

    def validate(self, context, parameters):
        require_resolver(context)
        return super().validate(replace(context, input_schema_ref="2.0"), parameters)

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        self.validate(context, parameters)
        terms = _load_terms(parameters)
        detector = DictionaryDetector(
            terms,
            parameters.get("unicode_normalization", "NFKC"),
            parameters.get("case_sensitive", True),
        )
        return execute_resolved_detector(
            context,
            parameters,
            detector,
            "sensitive-dictionary-hits.jsonl",
            [
                "enabled local EXACT dictionary terms over eligible logical text fields",
                "UTF-8 Chinese, English, and mixed text",
            ],
            [
                "dictionary hits are candidate risks and not confirmed violations",
                "no traditional/simplified conversion, pinyin, homophone, fuzzy matching, or semantic disambiguation",
                "normalization is per Unicode code point so original coordinates remain explicit",
                "HTML offsets use extracted visible-text coordinates",
            ],
        )


class LogicalStructuredSensitiveElementCalculator(StructuredSensitiveElementCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(policy_resolved_fields.__file__),
        )

    def validate(self, context, parameters):
        require_resolver(context)
        return super().validate(replace(context, input_schema_ref="2.0"), parameters)

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        self.validate(context, parameters)
        enabled = frozenset(parameters.get("enabled_categories", list(CATEGORIES)))
        return execute_resolved_detector(
            context,
            parameters,
            StructuredDetector(enabled),
            "structured-sensitive-element-hits.jsonl",
            [
                "seven configurable deterministic structured-sensitive-element candidate types",
                "full eligible logical text fields",
            ],
            [
                "format validity does not establish privacy leakage or non-compliance",
                "home addresses are heuristic SUSPECTED candidates",
                "HTML offsets use extracted visible-text coordinates",
                "no external validation, geocoding, DNS, network lookup, or model inference",
            ],
        )

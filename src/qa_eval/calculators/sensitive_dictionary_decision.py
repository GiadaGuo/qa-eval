"""Record-level decisions for the v3 sensitive dictionary matcher."""

from collections import Counter
from dataclasses import asdict
from pathlib import Path

from qa_eval.calculators.policy_resolved_fields import TEXT_FIELDS, field
from qa_eval.calculators.policy_sensitive_logical import (
    LogicalSensitiveDictionaryMatchCalculator,
)
from qa_eval.calculators.protocol import (
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.calculators.safety_candidate_common import _VisibleTextParser
from qa_eval.calculators.sensitive_dictionary_match import (
    DictionaryDetector,
    _load_terms,
)
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)

ARTIFACT = "sensitive-dictionary-decisions.jsonl"
RULE = "SENSITIVE_DICTIONARY_THRESHOLD"


class PositionedVisibleText(_VisibleTextParser):
    def extract(self, value):
        self.raw = value
        self.line_starts = [0] + [
            index + 1 for index, char in enumerate(value) if char == "\n"
        ]
        self.spans = []
        self.length = 0
        return super().extract(value)

    def handle_data(self, data):
        if not self.hidden_depth:
            line, column = self.getpos()
            raw_start = self.line_starts[line - 1] + column
            if self.raw.startswith(data, raw_start):
                self.spans.append((self.length, self.length + len(data), raw_start))
            self.length += len(data)
        super().handle_data(data)


class SensitiveDictionaryDecisionCalculator(LogicalSensitiveDictionaryMatchCalculator):
    version = "1.2.0"

    def source_paths(self):
        return (*super().source_paths(), Path(__file__))

    def validate(self, context, parameters):
        threshold = parameters.get("record_hit_threshold", 1)
        if type(threshold) is not int or threshold < 1:
            raise CalculatorValidationError(
                "record_hit_threshold must be an integer >= 1"
            )
        warnings = super().validate(context, parameters)
        if not _load_terms(parameters):
            raise CalculatorValidationError(
                "An enabled sensitive dictionary is required"
            )
        return warnings

    def execute(self, context, parameters):
        self.validate(context, parameters)
        detector = DictionaryDetector(
            _load_terms(parameters),
            parameters.get("unicode_normalization", "NFKC"),
            parameters.get("case_sensitive", True),
        )
        threshold = parameters.get("record_hit_threshold", 1)
        parser = PositionedVisibleText()
        counts, categories = Counter(), Counter()
        context.output_dir.mkdir(parents=True, exist_ok=True)
        path = context.output_dir / ARTIFACT
        with path.open("w", encoding="utf-8") as output:
            for record in context.resolved_records():
                row = self._decide(record, parameters, detector, parser, threshold)
                output.write(row.model_dump_json() + "\n")
                counts[row.status] += 1
                counts["hits"] += row.attributes["actual_value"]
                categories.update(hit["category"] for hit in row.attributes["hits"])
                if (
                    context.progress
                    and sum(counts[key] for key in ("1", "0", "NOT_EVALUATED")) % 1000
                    == 0
                ):
                    context.progress(
                        sum(counts[key] for key in ("1", "0", "NOT_EVALUATED")), {}
                    )
        compliant, failed, unknown = (
            counts[key] for key in ("1", "0", "NOT_EVALUATED")
        )
        evaluated = compliant + failed
        total = evaluated + unknown
        return NativeCalculatorOutput(
            {
                "input_count": total,
                "compliant_record_count": compliant,
                "non_compliant_record_count": failed,
                "not_evaluated_count": unknown,
                "evaluable_record_count": evaluated,
                "compliant_record_ratio": compliant / total if total else None,
                "evaluated_compliance_ratio": compliant / evaluated
                if evaluated
                else None,
                "evaluable_rate": evaluated / total if total else None,
                "sensitive_entity_hit_count": counts["hits"],
                "sensitive_entity_type_counts": dict(categories),
                "unknown_reasons": {"NO_ELIGIBLE_TEXT": unknown} if unknown else {},
            },
            status="OK" if evaluated else "NOT_APPLICABLE" if total else "MISSING",
            decisions=EvidenceOutput(
                path,
                "SENSITIVE_DICTIONARY_DECISIONS",
                DECISION_SCHEMA_REF,
                total,
                contains_raw_content=True,
                redaction_policy="matched text and bounded excerpt",
            ),
        )

    @staticmethod
    def _decide(record, parameters, detector, parser, threshold):
        sources = {}
        include = parameters.get("include_roles")
        exclude = parameters.get("exclude_roles") or ()
        include_ids = parameters.get("include_content_ids")
        for key, (role, fmt) in TEXT_FIELDS.items():
            if (
                (include is not None and role not in include)
                or role in exclude
                or (include_ids is not None and key not in include_ids)
            ):
                continue
            resolved = record.values[key]
            if resolved.status not in {"PRESENT", "EMPTY"} or resolved.origin is None:
                continue
            raw = field(record, key)
            if not isinstance(raw, str):
                continue
            text = parser.extract(raw) if fmt == "html" else raw
            if text.strip():
                # Prefer HTML's visible text over a second representation of the same field.
                path = tuple(resolved.origin.field_tokens)
                if path not in sources or fmt == "html":
                    sources[path] = (
                        key,
                        text,
                        resolved.origin,
                        fmt == "html" and raw != text,
                        parser.spans if fmt == "html" else [],
                    )

        hits, locations, origins = [], [], {}
        for path, (key, text, origin, visible, spans) in sources.items():
            span_index = 0
            origins[key] = asdict(origin)
            previous_end = 0
            ordered = sorted(
                detector.hits(text), key=lambda hit: (hit.start, -hit.end, hit.rule_ids)
            )
            for hit in ordered:
                if hit.start < previous_end:
                    continue
                previous_end = hit.end
                coordinate = (
                    "visible_text_unicode_codepoint"
                    if visible
                    else (
                        "resolved_text_unicode_codepoint"
                        if origin.transform
                        else "source_unicode_codepoint"
                    )
                )
                start, end = hit.start, hit.end
                while span_index < len(spans) and spans[span_index][1] <= start:
                    span_index += 1
                if visible and not origin.transform and span_index < len(spans):
                    left, right, offset = spans[span_index]
                    if left <= start < end <= right:
                        start, end = offset + start - left, offset + end - left
                        coordinate = "source_unicode_codepoint"
                evidence = {
                    **hit.metadata,
                    "category": hit.category,
                    "field_tokens": list(path),
                    "term": text[hit.start : hit.end],
                    "excerpt": text[
                        max(0, hit.start - 32) : min(len(text), hit.end + 32)
                    ],
                }
                hits.append(evidence)
                locations.append(
                    {
                        "content_id": key,
                        "coordinate_space": coordinate,
                        "start": start,
                        "end": end,
                        "attributes": evidence,
                    }
                )
        status = (
            ("0" if len(hits) >= threshold else "1") if sources else "NOT_EVALUATED"
        )
        sample = str(record.sample_id)
        return CalculatorDecisionV1(
            row_id=sample,
            sample_id=sample,
            target="SELF",
            rule_id=RULE,
            status=status,
            reason={
                "0": RULE,
                "1": "BELOW_RECORD_HIT_THRESHOLD",
                "NOT_EVALUATED": "NO_ELIGIBLE_TEXT",
            }[status],
            locations=locations,
            evidence_refs=[{"artifact_key": ARTIFACT, "row_id": sample}],
            attributes={
                "hits": hits,
                "actual_value": len(hits),
                "threshold": threshold,
                "source_origins": origins,
            },
        )

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from qa_eval.calculators.policy_structure_compliance.html_source_map import (
    validate_location,
)
from qa_eval.calculators.policy_structure_compliance.parsers import (
    PARSER_VERSION,
    parse_structure,
)
from qa_eval.calculators.policy_structure_compliance.schema import (
    GROUND_TRUTH_SCHEMA_REF,
    GROUND_TRUTH_TASK_KEY,
    GROUND_TRUTH_TASK_VERSION,
    FlagResult,
    PolicyStructureGroundTruthV1,
    StructureNode,
    StructureParseResult,
)
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import (
    Annotation,
    ContentItem,
    InlineTextPayload,
    SampleRecordV2,
)

FLAGS = ("parsing_success", "numbering_valid", "segmentation_correct", "path_locatable")
STATUSES = ("PASS", "FAIL", "NOT_APPLICABLE", "UNAVAILABLE")
DEFAULT_MAX_HTML_LENGTH = 300_000  # full-data p99.5 was 292,986 chars on 2026-11-06


class PolicyStructureComplianceCalculator:
    calculator_key = "policy_structure_compliance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (
            root / "__init__.py",
            root / "calculator.py",
            root / "html_source_map.py",
            root / "parsers.py",
            root / "schema.py",
        )

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "policy_structure_compliance requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        for key in ("html_role", "plain_role", "title_role"):
            if key in parameters and (
                not isinstance(parameters[key], str) or not parameters[key]
            ):
                raise CalculatorValidationError(f"{key} must be a non-empty string")
        maximum = parameters.get("max_html_length_chars", DEFAULT_MAX_HTML_LENGTH)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1000:
            raise CalculatorValidationError(
                "max_html_length_chars must be an integer >= 1000"
            )
        if parameters.get("long_text_policy", "REJECT") not in {"REJECT", "FULL_SCAN"}:
            raise CalculatorValidationError(
                "long_text_policy must be REJECT or FULL_SCAN"
            )
        limit = parameters.get("evidence_sample_limit", 0)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise CalculatorValidationError(
                "evidence_sample_limit must be an integer >= 0"
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        html_role = str(parameters.get("html_role", "article.body.html"))
        plain_role = str(parameters.get("plain_role", "article.body.plain"))
        title_role = str(parameters.get("title_role", "article.title"))
        maximum = int(parameters.get("max_html_length_chars", DEFAULT_MAX_HTML_LENGTH))
        full_scan = parameters.get("long_text_policy", "REJECT") == "FULL_SCAN"
        evidence_limit = int(parameters.get("evidence_sample_limit", 0))
        counts = {flag: Counter({status: 0 for status in STATUSES}) for flag in FLAGS}
        totals = Counter()
        input_count = processed = error_count = evidence_count = 0
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "policy-structure-flags.jsonl"
        with (
            context.input_path.open(encoding="utf-8") as source,
            evidence_path.open("w", encoding="utf-8") as evidence,
        ):
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                input_count += 1
                try:
                    sample = SampleRecordV2.model_validate_json(line)
                    row, results, measurements = self._evaluate_sample(
                        sample,
                        html_role=html_role,
                        plain_role=plain_role,
                        title_role=title_role,
                        maximum=maximum,
                        full_scan=full_scan,
                    )
                    processed += 1
                except Exception as exc:  # noqa: BLE001 - isolate individual records
                    error_count += 1
                    results = {
                        flag: FlagResult("UNAVAILABLE", "SAMPLE_PROCESSING_ERROR")
                        for flag in FLAGS
                    }
                    measurements = Counter()
                    row = {
                        "sample_id": f"line:{line_number}",
                        "content_id": None,
                        "diagnostics": [
                            {
                                "code": "SAMPLE_PROCESSING_ERROR",
                                "type": type(exc).__name__,
                            }
                        ],
                    }
                for flag, result in results.items():
                    counts[flag][result.status] += 1
                totals.update(measurements)
                row["flags"] = {
                    flag: {
                        "status": result.status,
                        "reason_code": result.reason_code,
                        "violations": result.violations,
                        "diagnostics": result.diagnostics,
                    }
                    for flag, result in results.items()
                }
                if evidence_limit == 0 or evidence_count < evidence_limit:
                    evidence.write(
                        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    evidence_count += 1
        if input_count == 0:
            return CalculatorOutput(
                status="MISSING",
                status_detail={"reason_code": "NO_INPUT_RECORDS"},
                observations=[],
                population={
                    "input_count": 0,
                    "evaluated_count": 0,
                    "excluded_count": 0,
                    "exclusions": [],
                },
                semantic_coverage=self._coverage(),
                diagnostics=diagnostics,
            )
        observations = self._observations(counts, totals)
        return CalculatorOutput(
            status="PARTIAL"
            if any(counts[flag]["UNAVAILABLE"] for flag in FLAGS)
            else "OK",
            status_detail={"reason_code": "SOME_FLAGS_UNAVAILABLE"}
            if any(counts[flag]["UNAVAILABLE"] for flag in FLAGS)
            else {},
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": processed,
                "excluded_count": input_count - processed,
                "exclusions": (
                    [{"reason": "sample_processing_error", "count": error_count}]
                    if error_count
                    else []
                ),
            },
            semantic_coverage=self._coverage(),
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="POLICY_STRUCTURE_FLAGS",
                    schema_ref="qa-eval://schemas/evidence/policy-structure-flags/1.0.0",
                    record_count=evidence_count,
                    error_count=error_count,
                    contains_raw_content=False,
                    redaction_policy="identifiers-coordinates-and-bounded-diagnostics-no-excerpts",
                    attributes={
                        "input_record_count": input_count,
                        "evidence_capped": evidence_count < input_count,
                        "parser_version": PARSER_VERSION,
                    },
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "flag_status_counts",
                    "counts": {key: dict(value) for key, value in counts.items()},
                },
            ],
        )

    @staticmethod
    def _content(sample: SampleRecordV2, role: str) -> ContentItem | None:
        return next((item for item in sample.contents if item.role == role), None)

    @classmethod
    def _text(cls, sample: SampleRecordV2, role: str) -> tuple[str | None, str | None]:
        item = cls._content(sample, role)
        if (
            item is None
            or item.presence != "PRESENT"
            or not isinstance(item.payload, InlineTextPayload)
        ):
            return None, None
        return item.payload.text, item.content_id

    def _evaluate_sample(
        self,
        sample: SampleRecordV2,
        *,
        html_role: str,
        plain_role: str,
        title_role: str,
        maximum: int,
        full_scan: bool,
    ):
        html_text, html_id = self._text(sample, html_role)
        plain_text, plain_id = self._text(sample, plain_role)
        title, _title_id = self._text(sample, title_role)
        content_id = html_id or plain_id
        if html_text is not None and len(html_text) > maximum and not full_scan:
            unavailable = {
                flag: FlagResult("UNAVAILABLE", "HTML_LENGTH_LIMIT_EXCEEDED")
                for flag in FLAGS
            }
            return (
                {
                    "sample_id": sample.sample_id,
                    "content_id": content_id,
                    "parser_version": PARSER_VERSION,
                    "diagnostics": [
                        {
                            "code": "HTML_LENGTH_LIMIT_EXCEEDED",
                            "observed": len(html_text),
                            "expected_max": maximum,
                        }
                    ],
                },
                unavailable,
                Counter(),
            )
        if not (html_text or plain_text):
            unavailable = {
                flag: FlagResult("UNAVAILABLE", "BODY_MISSING") for flag in FLAGS
            }
            return (
                {
                    "sample_id": sample.sample_id,
                    "content_id": content_id,
                    "parser_version": PARSER_VERSION,
                    "diagnostics": [],
                },
                unavailable,
                Counter(),
            )
        parsed = parse_structure(
            html_source=html_text,
            plain_source=plain_text,
            title=title or "",
            metadata=sample.metadata,
        )
        parsing = self._parsing(parsed)
        numbering, number_counts = self._numbering(parsed, parsing)
        segmentation = self._segmentation(parsed, sample.annotations, content_id)
        path, exact_counts = self._path(parsed, html_text, parsing)
        row = {
            "sample_id": sample.sample_id,
            "content_id": content_id,
            "check_item_ids": ["BG-02-03", "BG-02-04", "BG-02-05"],
            "parser_version": PARSER_VERSION,
            "document_type": {
                "value": parsed.document_type,
                "source": parsed.document_type_source,
                "confidence": parsed.document_type_confidence,
            },
            "mode": parsed.mode,
            "mode_candidates": parsed.mode_candidates,
            "node_count": len(parsed.nodes),
            "diagnostics": parsed.diagnostics,
        }
        return (
            row,
            {
                "parsing_success": parsing,
                "numbering_valid": numbering,
                "segmentation_correct": segmentation,
                "path_locatable": path,
            },
            number_counts + exact_counts,
        )

    @staticmethod
    def _parsing(parsed: StructureParseResult) -> FlagResult:
        if not parsed.blocks:
            return FlagResult("UNAVAILABLE", "NO_VISIBLE_BODY")
        if any(node.ordinal is None or node.ordinal < 1 for node in parsed.nodes):
            return FlagResult("FAIL", "INVALID_STRUCTURE_ORDINAL")
        return FlagResult(
            "PASS",
            "STRUCTURE_PARSED",
            diagnostics={"mode": parsed.mode, "node_count": len(parsed.nodes)},
        )

    @staticmethod
    def _numbering(
        parsed: StructureParseResult, parsing: FlagResult
    ) -> tuple[FlagResult, Counter]:
        totals = Counter()
        if parsing.status != "PASS":
            return FlagResult("UNAVAILABLE", "PARSING_NOT_AVAILABLE"), totals
        if not parsed.nodes:
            return FlagResult("NOT_APPLICABLE", "NO_NUMBERED_STRUCTURE"), totals
        groups: dict[str, list[StructureNode]] = defaultdict(list)
        for node in parsed.nodes:
            groups[node.numbering_scope or "unknown"].append(node)
        violations = []
        for scope, nodes in groups.items():
            previous = None
            for node in nodes:
                current = node.ordinal or 0
                coordinate = {
                    "check_item_id": "BG-02-04",
                    "node_path": node.node_id,
                    "source_location": asdict(node.location),
                }
                if previous is None:
                    if current != 1:
                        violations.append(
                            {
                                **coordinate,
                                "violation_code": "NUMBER_START_ANOMALY",
                                "scope": scope,
                                "observed": current,
                                "expected": 1,
                            }
                        )
                        totals["numbering_start_anomaly_count"] += 1
                elif current == previous:
                    violations.append(
                        {
                            **coordinate,
                            "violation_code": "NUMBER_DUPLICATE",
                            "scope": scope,
                            "observed": current,
                        }
                    )
                    totals["duplicate_number_count"] += 1
                elif current < previous:
                    violations.append(
                        {
                            **coordinate,
                            "violation_code": "NUMBER_REVERSE",
                            "scope": scope,
                            "observed": current,
                            "previous": previous,
                        }
                    )
                    totals["reverse_number_count"] += 1
                elif current > previous + 1:
                    missing = list(range(previous + 1, current))
                    violations.append(
                        {
                            **coordinate,
                            "violation_code": "NUMBER_GAP",
                            "scope": scope,
                            "observed": current,
                            "previous": previous,
                            "missing_ordinals": missing,
                        }
                    )
                    totals["skipped_number_count"] += 1
                    totals["missing_number_count"] += len(missing)
                previous = current
        totals["numbering_defect_count"] += len(violations)
        return FlagResult(
            "FAIL" if violations else "PASS",
            "NUMBERING_DEFECTS_FOUND" if violations else "NUMBERING_CONTINUOUS",
            violations,
        ), totals

    @staticmethod
    def _ground_truth(
        annotations: list[Annotation], content_id: str | None
    ) -> Annotation | None:
        return next(
            (
                item
                for item in annotations
                if item.status == "FINAL"
                and item.provenance.method in {"HUMAN", "IMPORTED"}
                and item.task.key == GROUND_TRUTH_TASK_KEY
                and item.task.version == GROUND_TRUTH_TASK_VERSION
                and any(target.content_id == content_id for target in item.targets)
                and item.value.get("schema_ref") == GROUND_TRUTH_SCHEMA_REF
            ),
            None,
        )

    @classmethod
    def _segmentation(
        cls,
        parsed: StructureParseResult,
        annotations: list[Annotation],
        content_id: str | None,
    ) -> FlagResult:
        truth = cls._ground_truth(annotations, content_id)
        if truth is None:
            return FlagResult("UNAVAILABLE", "GROUND_TRUTH_MISSING")
        try:
            expected_nodes = PolicyStructureGroundTruthV1.model_validate(
                truth.value
            ).nodes
            expected_by_id = {item.node_id: item for item in expected_nodes}

            def expected_signature(item):
                parent = expected_by_id.get(item.parent_id or "")
                parent_signature = (
                    None
                    if parent is None
                    else (
                        parent.node_type,
                        parent.normalized_text_start,
                        parent.normalized_text_end,
                    )
                )
                return (
                    item.node_type,
                    item.normalized_text_start,
                    item.normalized_text_end,
                    parent_signature,
                )

            expected = {expected_signature(item) for item in expected_nodes}
        except (KeyError, TypeError, ValueError):
            return FlagResult("UNAVAILABLE", "GROUND_TRUTH_INVALID")
        predicted_by_id = {node.node_id: node for node in parsed.nodes}
        predicted = {
            (
                node.node_type,
                node.location.normalized_text_start,
                node.location.normalized_text_end,
                (
                    None
                    if node.parent_id is None
                    else (
                        predicted_by_id[node.parent_id].node_type,
                        predicted_by_id[node.parent_id].location.normalized_text_start,
                        predicted_by_id[node.parent_id].location.normalized_text_end,
                    )
                ),
            )
            for node in parsed.nodes
        }
        tp = len(expected & predicted)
        diagnostics = {
            "tp": tp,
            "fp": len(predicted - expected),
            "fn": len(expected - predicted),
        }
        passed = expected == predicted
        return FlagResult(
            "PASS" if passed else "FAIL",
            "SEGMENTATION_MATCH" if passed else "SEGMENTATION_MISMATCH",
            diagnostics=diagnostics,
        )

    @staticmethod
    def _path(
        parsed: StructureParseResult, html_text: str | None, parsing: FlagResult
    ) -> tuple[FlagResult, Counter]:
        totals = Counter()
        if parsing.status != "PASS":
            return FlagResult("UNAVAILABLE", "PARSING_NOT_AVAILABLE"), totals
        if not parsed.nodes:
            return FlagResult("NOT_APPLICABLE", "NO_STRUCTURE_NODES"), totals
        if html_text is None:
            return FlagResult("UNAVAILABLE", "HTML_SOURCE_MISSING"), totals
        invalid = []
        exact = 0
        for node in parsed.nodes:
            block = next(
                (item for item in parsed.blocks if item.location is node.location), None
            )
            valid = validate_location(
                html_text, node.location, block.text if block else None
            )
            if not valid:
                invalid.append(
                    {
                        "check_item_id": "BG-02-05",
                        "violation_code": "SOURCE_LOCATION_INVALID",
                        "node_path": node.node_id,
                        "source_location": asdict(node.location),
                    }
                )
            elif node.location.coordinate_status == "EXACT":
                exact += 1
        totals["exact_location_node_count"] += exact
        totals["locatable_node_count"] += len(parsed.nodes) - len(invalid)
        totals["location_node_count"] += len(parsed.nodes)
        return FlagResult(
            "FAIL" if invalid else "PASS",
            "LOCATION_INVALID" if invalid else "LOCATION_VERIFIED",
            invalid,
            {"exact_nodes": exact, "total_nodes": len(parsed.nodes)},
        ), totals

    @staticmethod
    def _observations(counts: dict[str, Counter], totals: Counter) -> list[JsonObject]:
        observations: list[JsonObject] = []
        rate_keys = {
            "parsing_success": "parsing_success_rate",
            "numbering_valid": "numbering_compliance_rate",
            "segmentation_correct": "segmentation_correct_rate",
            "path_locatable": "path_locatable_rate",
        }
        for flag in FLAGS:
            status_counts = {status: counts[flag][status] for status in STATUSES}
            observations.append(
                {
                    "key": f"{flag}_status_counts",
                    "value": status_counts,
                    "value_type": "OBJECT",
                    "unit": "record",
                }
            )
            denominator = status_counts["PASS"] + status_counts["FAIL"]
            observations.append(
                {
                    "key": f"{flag}_evaluable_count",
                    "value": denominator,
                    "value_type": "NUMBER",
                    "unit": "record",
                }
            )
            if denominator:
                observations.append(
                    {
                        "key": rate_keys[flag],
                        "value": status_counts["PASS"] / denominator,
                        "value_type": "NUMBER",
                        "unit": "ratio",
                        "numerator": status_counts["PASS"],
                        "denominator": denominator,
                    }
                )
            total = sum(status_counts.values())
            if total:
                observations.append(
                    {
                        "key": f"{flag}_coverage_rate",
                        "value": denominator / total,
                        "value_type": "NUMBER",
                        "unit": "ratio",
                        "numerator": denominator,
                        "denominator": total,
                    }
                )
        for key in (
            "numbering_defect_count",
            "skipped_number_count",
            "duplicate_number_count",
            "missing_number_count",
            "reverse_number_count",
        ):
            observations.append(
                {
                    "key": key,
                    "value": totals[key],
                    "value_type": "NUMBER",
                    "unit": "event" if key != "missing_number_count" else "ordinal",
                }
            )
        node_denominator = totals["location_node_count"]
        if node_denominator:
            observations.append(
                {
                    "key": "exact_location_rate",
                    "value": totals["exact_location_node_count"] / node_denominator,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": totals["exact_location_node_count"],
                    "denominator": node_denominator,
                }
            )
        return observations

    @staticmethod
    def _coverage() -> JsonObject:
        return {
            "level": "PARTIAL",
            "covered": [
                "HTML/plain block structure",
                "legal and outline numbering",
                "source HTML Unicode coordinates",
                "versioned human/imported segmentation annotations",
            ],
            "excluded": [
                "attachment download/OCR",
                "legal semantic completeness",
                "unannotated segmentation accuracy",
            ],
            "limitations": [
                "document type is a weak prior",
                "unknown legal structures remain unavailable rather than quality failures",
            ],
        }


__all__ = ["PolicyStructureComplianceCalculator"]

from __future__ import annotations

import hashlib
import html.parser
import json
import re
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.sensitive_entity_detection.provider import IsolatedUIEProvider
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

MODEL_ID = "PaddleNLP/uie-mini-v1.1-via-spy-cn"
MODEL_REVISION = "spy-cn/uie_pytorch@bfe4306416a771438525774b16ba62163f1da333"
RULESET_VERSION = "bg-06-01-rules@1.0.0"
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


class _VisibleText(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def extract(self, value: str) -> str:
        self.reset()
        self.parts = []
        self.hidden = 0
        self.feed(value)
        self.close()
        return "".join(self.parts)


def _valid_id(value: str) -> bool:
    try:
        year, month, day = int(value[6:10]), int(value[10:12]), int(value[12:14])
        import datetime

        datetime.date(year, month, day)
    except ValueError:
        return False
    return (
        value[:6] != "000000"
        and value[14:17] != "000"
        and _ID_CHECK[sum(int(x) * w for x, w in zip(value[:17], _ID_WEIGHTS)) % 11]
        == value[-1].upper()
    )


def _rules(text: str) -> list[dict]:
    hits = [
        {
            "category": "MOBILE_PHONE_CN",
            "start": m.start(),
            "end": m.end(),
            "channels": ["RULE"],
            "rule_ids": ["phone.mobile.cn.v1"],
        }
        for m in _PHONE.finditer(text)
    ]
    hits += [
        {
            "category": "PRC_RESIDENT_ID",
            "start": m.start(),
            "end": m.end(),
            "channels": ["RULE"],
            "rule_ids": ["id.prc.18.mod11.v1"],
        }
        for m in _ID.finditer(text)
        if _valid_id(m.group())
    ]
    return hits


def _windows(text: str, size: int, overlap: int):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def _mask(text: str, category: str) -> str:
    if category == "MOBILE_PHONE_CN" and len(text) >= 7:
        return text[:3] + "****" + text[-4:]
    if category == "PRC_RESIDENT_ID" and len(text) >= 8:
        return text[:3] + "***********" + text[-4:]
    if category == "INTERNAL_INSTRUCTION":
        return "[INTERNAL_INSTRUCTION_REDACTED]"
    return "[SENSITIVE_ENTITY_REDACTED]"


def _sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _evidence_row_id(identity: JsonObject) -> str:
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class SensitiveEntityDetectionCalculator:
    calculator_key = "bg_06_01_sensitive_entity_detection"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return tuple(
            root / x
            for x in ("calculator.py", "provider.py", "ner_worker.py", "__init__.py")
        )

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0" or not context.input_path.is_file():
            raise CalculatorValidationError(
                "SampleRecord 2.0 normalized input is required"
            )
        for key in ("ner_python", "model_dir", "vendor_src", "model_checksum"):
            if not isinstance(parameters.get(key), str) or not parameters[key]:
                raise CalculatorValidationError(f"{key} is required")
        size = parameters.get("window_chars", 320)
        overlap = parameters.get("window_overlap_chars", 64)
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 64 <= size <= 1000
        ):
            raise CalculatorValidationError("window_chars must be 64..1000")
        if (
            isinstance(overlap, bool)
            or not isinstance(overlap, int)
            or not 0 <= overlap < size
        ):
            raise CalculatorValidationError(
                "window_overlap_chars must be smaller than window_chars"
            )
        for key, default in (
            ("max_records", 20),
            ("evidence_limit", 100),
            ("inference_timeout_seconds", 30),
        ):
            value = parameters.get(key, default)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise CalculatorValidationError(f"{key} must be positive")
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        self.validate(context, parameters)
        max_records = int(parameters.get("max_records", 20))
        # Retained only so historical Contracts remain valid. Evidence is now
        # always persisted completely; preview limits belong to query/UI APIs.
        configured_legacy_evidence_limit = int(parameters.get("evidence_limit", 100))
        size = int(parameters.get("window_chars", 320))
        overlap = int(parameters.get("window_overlap_chars", 64))
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "sensitive-entity-hits.jsonl"
        input_count = evaluated = selected = scanned = eligible = affected = (
            hits_total
        ) = written = 0
        type_counts = Counter()
        exclusions = Counter()
        parser = _VisibleText()
        provider = IsolatedUIEProvider(
            python=Path(parameters["ner_python"]),
            model_dir=Path(parameters["model_dir"]),
            vendor_src=Path(parameters["vendor_src"]),
            checksum=parameters["model_checksum"],
            timeout=float(parameters.get("inference_timeout_seconds", 30)),
        )
        try:
            with (
                context.input_path.open(encoding="utf-8") as source,
                evidence_path.open("w", encoding="utf-8") as evidence,
            ):
                for line_no, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    input_count += 1
                    if input_count > max_records:
                        exclusions["SAMPLED_OUT_MAX_RECORDS"] += 1
                        continue
                    try:
                        sample = SampleRecordV2.model_validate_json(line)
                    except (ValidationError, ValueError) as exc:
                        raise CalculatorValidationError(
                            "invalid SampleRecord", line_number=line_no
                        ) from exc
                    by_role = {
                        x.role: x
                        for x in sample.contents
                        if x.presence == "PRESENT"
                        and x.modality == "text"
                        and isinstance(x.payload, InlineTextPayload)
                        and x.payload.text
                    }
                    chosen = []
                    body = by_role.get("article.body.html") or by_role.get(
                        "article.body.plain"
                    )
                    if body:
                        chosen.append(("article.body", body))
                    if title := by_role.get("article.title"):
                        chosen.append(("article.title", title))
                    if not chosen:
                        exclusions["NO_ELIGIBLE_TEXT"] += 1
                        continue
                    record_hits = []
                    for logical_field, item in chosen:
                        assert isinstance(item.payload, InlineTextPayload)
                        text = (
                            parser.extract(item.payload.text)
                            if item.payload.format == "html"
                            else item.payload.text
                        )
                        coordinate = (
                            "VISIBLE_TEXT_UNICODE_CODE_POINT"
                            if item.payload.format == "html"
                            else "CONTENT_TEXT_UNICODE_CODE_POINT"
                        )
                        selected += 1
                        eligible += len(text)
                        candidates = _rules(text)
                        for window_start, window in _windows(text, size, overlap):
                            entities = provider.infer(
                                window,
                                f"{sample.sample_id}:{item.content_id}:{window_start}",
                            )
                            for entity in entities:
                                candidates.append(
                                    {
                                        **entity,
                                        "start": entity["start"] + window_start,
                                        "end": entity["end"] + window_start,
                                        "channels": ["NER"],
                                        "rule_ids": [],
                                    }
                                )
                        merged = {}
                        for hit in candidates:
                            key = (hit["category"], hit["start"], hit["end"])
                            current = merged.setdefault(
                                key,
                                {
                                    **hit,
                                    "channels": [],
                                    "rule_ids": [],
                                    "model_score": None,
                                    "original_label": None,
                                },
                            )
                            current["channels"] = sorted(
                                set(current["channels"] + hit.get("channels", []))
                            )
                            current["rule_ids"] = sorted(
                                set(current["rule_ids"] + hit.get("rule_ids", []))
                            )
                            if "probability" in hit:
                                current["model_score"] = hit["probability"]
                                current["original_label"] = hit.get("original_label")
                        for hit in merged.values():
                            record_hits.append(hit)
                            type_counts[hit["category"]] += 1
                            hits_total += 1
                            value = text[hit["start"] : hit["end"]]
                            identity = {
                                "schema_ref": "qa-eval://schemas/evidence/sensitive-entity-hits/1.1.0",
                                "dataset_version_id": str(context.dataset_version_id),
                                "sample_id": sample.sample_id,
                                "content_id": item.content_id,
                                "category": hit["category"],
                                "start": hit["start"],
                                "end": hit["end"],
                                "coordinate_space": coordinate,
                                "channels": hit["channels"],
                                "rule_ids": hit["rule_ids"],
                                "model_id": MODEL_ID
                                if "NER" in hit["channels"]
                                else None,
                            }
                            row = {
                                "row_id": _evidence_row_id(identity),
                                "sample_id": sample.sample_id,
                                "content_id": item.content_id,
                                "field_path": (
                                    f"$.contents[content_id={item.content_id}].payload.text"
                                ),
                                "logical_field": logical_field,
                                "category": hit["category"],
                                "start": hit["start"],
                                "end": hit["end"],
                                "offset_unit": "UNICODE_CODE_POINT",
                                "coordinate_space": coordinate,
                                "text_version": _sha256_text(text),
                                "mapping_precision": "EXACT_VISIBLE_TEXT"
                                if item.payload.format == "html"
                                else "EXACT",
                                "channels": hit["channels"],
                                "masked_excerpt": _mask(value, hit["category"]),
                                "model_id": identity["model_id"],
                                "model_score": hit["model_score"],
                                "model_original_label": hit["original_label"],
                                "rule_ids": hit["rule_ids"],
                                "review_status": "UNREVIEWED",
                            }
                            evidence.write(
                                json.dumps(row, ensure_ascii=True, sort_keys=True)
                                + "\n"
                            )
                            written += 1
                        scanned += len(text)
                    evaluated += 1
                    if record_hits:
                        affected += 1
        finally:
            provider.close()
        if input_count == 0:
            return CalculatorOutput(
                observations=[],
                population={
                    "input_count": 0,
                    "evaluated_count": 0,
                    "excluded_count": 0,
                    "exclusions": [],
                },
                semantic_coverage={
                    "level": "PARTIAL",
                    "covered": [],
                    "excluded": ["no input"],
                    "limitations": [],
                },
                status="MISSING",
                status_detail={"reason_code": "NO_INPUT_RECORDS"},
            )
        if evaluated == 0:
            return CalculatorOutput(
                observations=[],
                population={
                    "input_count": input_count,
                    "evaluated_count": 0,
                    "excluded_count": input_count,
                    "exclusions": [
                        {"reason": k, "count": v} for k, v in exclusions.items()
                    ],
                },
                semantic_coverage={
                    "level": "PARTIAL",
                    "covered": [],
                    "excluded": ["no eligible text"],
                    "limitations": [],
                },
                status="NOT_APPLICABLE",
                status_detail={"reason_code": "NO_ELIGIBLE_TEXT"},
            )
        coverage = scanned / eligible if eligible else 0
        observations = [
            {
                "key": "sensitive_entity_hit_count",
                "value": hits_total,
                "value_type": "NUMBER",
                "unit": "entity",
            },
            {
                "key": "sensitive_entity_type_counts",
                "value": dict(sorted(type_counts.items())),
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
                "value": coverage,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": scanned,
                "denominator": eligible,
            },
        ]
        partial_scope = input_count > max_records
        return CalculatorOutput(
            status="PARTIAL" if partial_scope else "OK",
            status_detail={
                "partial_scope": partial_scope,
                "scope_reason": "MAX_RECORDS"
                if partial_scope
                else "FULL_INPUT_POPULATION",
                "evaluated_record_count": evaluated,
                "input_record_count": input_count,
            },
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": evaluated,
                "excluded_count": input_count - evaluated,
                "exclusions": [
                    {"reason": k, "count": v} for k, v in exclusions.items()
                ],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "selected text roles; deterministic ID/phone rules; UIE schema spans"
                ],
                "excluded": ["raw HTML coordinate mapping", "model calibration"],
                "limitations": [
                    "max_records sampling",
                    "UIE zero-shot recall is limited",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="SENSITIVE_ENTITY_HITS",
                    schema_ref="qa-eval://schemas/evidence/sensitive-entity-hits/1.1.0",
                    record_count=written,
                    subject_scope="CONTENT",
                    contains_raw_content=False,
                    redaction_policy="category-mask-no-raw-sensitive-value",
                    attributes={
                        "detected_count": hits_total,
                        "emitted_count": written,
                        "truncated": False,
                        "truncation_reason": None,
                        "completeness_status": "COMPLETE",
                        "counting_unit": "MERGED_ENTITY_HIT",
                        "one_record_per_detected_fact": True,
                        "row_identity_version": "1.0",
                        "location_schema_version": "1.0",
                        "legacy_evidence_limit_ignored": configured_legacy_evidence_limit,
                        "total_hit_count": hits_total,
                        "written_hit_count": written,
                        "evidence_capped": False,
                    },
                )
            ],
            diagnostics=[
                {
                    "requirement_id": "BG-06-01",
                    "selected_text_count": selected,
                    "scanned_character_count": scanned,
                    "eligible_character_count": eligible,
                    "partial_scope": partial_scope,
                    "max_records": max_records,
                    "channel_status": {"rules": "OK", "ner": "OK"},
                    "model_id": MODEL_ID,
                    "model_revision": MODEL_REVISION,
                    "model_checksum": parameters["model_checksum"],
                    "execution_device": "cpu",
                    "ruleset_version": RULESET_VERSION,
                }
            ],
        )

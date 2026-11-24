"""DIRECT native Calculator for within-body consistency."""

import hashlib
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from qa_eval.calculators.content_consistency.protocol_support import (
    implementation_fingerprint,
    source_paths,
)
from qa_eval.calculators.content_consistency.recall import (
    Encoder,
    candidates,
    open_cache,
)
from qa_eval.calculators.content_consistency.rules import ATTRIBUTES, compare, extract
from qa_eval.calculators.content_consistency.text import passages
from qa_eval.calculators.protocol import (
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF
from qa_eval.contracts.datasets import SampleRecordV2

KEY = "content_consistency"
VERSION = "2.0.0"
RULESET = "0206-BODY-2"
DECISIONS = "content-consistency-decisions.jsonl"
WEIGHTS = {"severe": 0.4, "general": 0.2, "minor": 0.1}
SEVERITY_ORDER = {name: rank for rank, name in enumerate(reversed(WEIGHTS), 1)}
REASONS = {
    "DISJOINT_YEAR_SCOPES": "相同事项分别适用于明确且不同的年度",
    "DISJOINT_INTERVALS": "同一对象、事项和适用范围内的标准化约束区间不相交",
    "REQUIRED_AND_FORBIDDEN": "同一适用范围内，同一行为既被要求又被禁止",
    "INCOMPATIBLE_MODALITIES": "同一适用范围内，同一行为的义务或权限不兼容",
    "OPPOSITE_ASSERTIONS": "同一适用范围内，同一行为同时被肯定和否定",
    "COMPATIBLE_INTERVALS": "标准化约束区间存在交集",
    "DISTINCT_CONSTRAINT_KINDS": "同一对象和适用范围内，约束针对明确不同的属性类别",
    "DISTINCT_ACTION_KINDS": "同一对象和适用范围内，明确识别的行为类别不同",
    "COMPATIBLE_MODALITIES": "义务或权限存在兼容解释",
    "ASSERTION_AND_OBLIGATION": "事实陈述与规范义务不可直接比较",
    "REFERENCE_EXCEPTION_OR_TRANSITION": "存在援引、例外、废止或过渡语境",
    "ATTRIBUTE_NOT_ALIGNED": "无法确认属性一致",
    "OBJECT_OR_ACTION_NOT_ALIGNED": "无法确认对象和事项一致",
    "APPLICABILITY_NOT_ALIGNED": "无法确认适用条件或时间范围一致",
    "UNITS_NOT_COMPARABLE": "计量单位无法安全换算",
}


def _ratio(n, d):
    return n / d if d else None


def evaluate(record, parameters, encoder=None):
    blocks, metadata, excluded = passages(record)
    facts, supported_blocks, units, unresolved = extract(blocks)
    summaries = Counter()
    reasons = Counter()
    conflicts = {}
    conflict_evidence_slots = {}
    retained = {"CONTRADICTION": [], "NON_CONTRADICTION": [], "UNKNOWN": []}
    limit = parameters.get("evidence_per_outcome", 4)
    lexical_truncated = semantic_truncated = 0
    if unresolved:
        reasons["UNSUPPORTED_CONSTRAINT_FORM"] = unresolved
    for index, lexical, semantic, lexical_truncated, semantic_truncated in candidates(
        facts, encoder, parameters.get("top_k", 8), parameters.get("posting_limit", 32)
    ):
        summaries["rule_candidates"] += len(lexical)
        summaries["vector_candidates"] += len(semantic)
        for old in sorted(set(lexical).union(semantic)):
            a, b = facts[old], facts[index]
            summaries["candidates"] += 1
            outcome, reason, severity = compare(a, b)
            summaries[outcome] += 1
            if outcome == "UNKNOWN":
                reasons[reason] += 1
            if severity:
                # All incompatible formulations of one attribute in the same scope
                # incur one penalty. Repeated copies still count in candidate statistics.
                family = "assertion" if reason == "OPPOSITE_ASSERTIONS" else a.kind
                key = (a.key, a.unit, a.condition, a.time_scope, family)
                if SEVERITY_ORDER[severity] <= SEVERITY_ORDER.get(
                    conflicts.get(key), 0
                ):
                    continue
                conflicts[key] = severity
                if key in conflict_evidence_slots:
                    slot = conflict_evidence_slots[key]
                elif len(retained[outcome]) < limit:
                    slot = len(retained[outcome])
                    conflict_evidence_slots[key] = slot
                else:
                    continue
            else:
                slot = len(retained[outcome])
            if slot < limit:
                pair = {
                    "outcome": outcome,
                    "rule_id": f"{RULESET}:{reason}",
                    "basis": REASONS[reason],
                    "conflict_type": a.kind if severity else None,
                    "severity": severity,
                    "left": a.evidence(),
                    "right": b.evidence(),
                }
                if slot == len(retained[outcome]):
                    retained[outcome].append(pair)
                else:
                    retained[outcome][slot] = pair
    if not excluded and not (summaries["NON_CONTRADICTION"] or conflicts):
        excluded = (
            "NO_COMPARABLE_CONSTRAINTS"
            if len(facts) >= 2
            else "INSUFFICIENT_STRUCTURED_EVIDENCE"
        )
    weights = WEIGHTS | parameters.get("weights", {})
    total = sum(
        (Decimal(str(weights[value])) for value in conflicts.values()), Decimal(0)
    )
    score = max(Decimal(0), 1 - total)
    threshold = Decimal(str(parameters.get("classification_threshold", 0.7)))
    label = "NOT_EVALUATED" if excluded else int(score >= threshold)
    pairs = [pair for group in retained.values() for pair in group]
    candidate_count = summaries["candidates"]
    locations = []
    for pair in retained["CONTRADICTION"]:
        for side in ("left", "right"):
            loc = pair[side]["source_span"]
            if spans := loc["source_spans"]:
                locations.append(
                    {
                        "content_id": loc["content_id"],
                        "coordinate_space": loc["coordinate_space"],
                        "start": spans[0]["start"],
                        "end": spans[-1]["end"],
                        "attributes": loc | {"rule_id": pair["rule_id"], "side": side},
                    }
                )
    return {
        "row_id": record.sample_id,
        "sample_id": record.sample_id,
        "rule_id": RULESET,
        "status": str(label),
        "target": "article.body",
        "reason": excluded
        or (
            "CONFIRMED_CONTRADICTION"
            if conflicts
            else "NO_CONTRADICTION_IN_COVERED_RULES"
        ),
        "locations": locations,
        "evidence_refs": [{"artifact_key": DECISIONS, "row_id": record.sample_id}],
        "attributes": {
            **metadata,
            "label": label,
            "score": float(score) if not excluded else None,
            "exclusion": excluded,
            "structured_facts": len(facts),
            "sentence_units": units,
            "parsed_blocks": len(blocks),
            "parsed_block_chars": sum(len(block.text) for block in blocks),
            "rule_supported_blocks": len(supported_blocks),
            "rule_block_coverage": _ratio(len(supported_blocks), len(blocks)),
            "body_processing_complete": bool(blocks)
            and not metadata.get("malformed_html"),
            "candidate_counts": dict(summaries),
            "candidate_completion": _ratio(
                sum(summaries[k] for k in retained), candidate_count
            ),
            "rule_truncated_queries": lexical_truncated,
            "vector_truncated_queries": semantic_truncated,
            "recall_coverage_risk": bool(
                lexical_truncated or semantic_truncated or (encoder and len(facts) >= 2)
            ),
            "unknown_reasons": dict(reasons),
            "conflict_count": len(conflicts),
            "conflicts_by_severity": dict(Counter(conflicts.values())),
            "pairs": pairs,
        },
    }


def _count(totals, distributions, row):
    data = row["attributes"]
    totals["input_count"] += 1
    if data["exclusion"]:
        totals["not_evaluated_count"] += 1
        distributions["exclusions"][data["exclusion"]] += 1
    else:
        totals["evaluable_record_count"] += 1
        totals[
            ("inconsistent_record_count", "consistent_record_count")[data["label"]]
        ] += 1
    totals["contradiction_record_count"] += bool(data["conflict_count"])
    totals["confirmed_conflict_count"] += data["conflict_count"]
    totals["candidate_risk_record_count"] += data["recall_coverage_risk"]
    totals["rule_truncated_record_count"] += bool(data["rule_truncated_queries"])
    totals["vector_truncated_record_count"] += bool(data["vector_truncated_queries"])
    totals["body_processed_record_count"] += data["body_processing_complete"]
    totals["parsed_block_count"] += data["parsed_blocks"]
    totals["rule_supported_block_count"] += data["rule_supported_blocks"]
    for key in ("candidates", "rule_candidates", "vector_candidates"):
        totals[f"{key}_count"] += data["candidate_counts"].get(key, 0)
    totals["processed_candidate_count"] += sum(
        data["candidate_counts"].get(key, 0)
        for key in ("CONTRADICTION", "NON_CONTRADICTION", "UNKNOWN")
    )
    distributions["unknown_reasons"].update(data["unknown_reasons"])


COUNTS = (
    "input_count",
    "consistent_record_count",
    "inconsistent_record_count",
    "not_evaluated_count",
    "evaluable_record_count",
    "contradiction_record_count",
    "confirmed_conflict_count",
    "candidate_risk_record_count",
    "body_processed_record_count",
    "parsed_block_count",
    "rule_supported_block_count",
    "candidates_count",
    "rule_candidates_count",
    "vector_candidates_count",
    "processed_candidate_count",
    "rule_truncated_record_count",
    "vector_truncated_record_count",
)
RATIOS = {
    "consistent_record_ratio": ("consistent_record_count", "evaluable_record_count"),
    "coverage": ("evaluable_record_count", "input_count"),
    "all_input_consistent_ratio": ("consistent_record_count", "input_count"),
    "contradiction_record_ratio": ("contradiction_record_count", "input_count"),
    "body_processing_coverage": ("body_processed_record_count", "input_count"),
    "rule_block_coverage": ("rule_supported_block_count", "parsed_block_count"),
    "candidate_completion": ("processed_candidate_count", "candidates_count"),
}


def _validate(parameters):
    if parameters.get("posting_limit", 32) < parameters.get("top_k", 8):
        raise CalculatorValidationError("posting_limit must be at least top_k")
    return []


def _checkpoint_path(context, parameters, encoder):
    directory = os.getenv("QA_EVAL_0206_CHECKPOINT_DIR")
    if not directory:
        return ":memory:"
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = hashlib.sha256(
        json.dumps(
            [
                str(context.dataset_version_id),
                context.metric_version,
                implementation_fingerprint(),
                parameters,
                encoder.identity if encoder else None,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return root / f"{identity}.sqlite3"


class BodyContentConsistencyCalculator:
    calculator_key = KEY
    version = VERSION
    source_paths = staticmethod(source_paths)

    def validate(self, context, parameters):
        return _validate(parameters)

    def execute(self, context, parameters):
        self.validate(context, parameters)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISIONS
        temporary = destination.with_suffix(".pending")
        totals = Counter()
        distributions = {"exclusions": Counter(), "unknown_reasons": Counter()}
        use_vectors = parameters.get("mode", "rule_only") == "rule_embedding"
        with open_cache(
            os.getenv("QA_EVAL_0206_EMBEDDING_CACHE") if use_vectors else None
        ) as conn:
            encoder = (
                Encoder(
                    parameters.get("embedding_backend", "concept"),
                    os.getenv("QA_EVAL_0206_BGE_MODEL_DIR"),
                    conn,
                    parameters.get("embedding_batch_size", 32),
                )
                if use_vectors
                else None
            )
            checkpoint_path = _checkpoint_path(context, parameters, encoder)
            resumed = self._stream(
                context,
                parameters,
                temporary,
                totals,
                distributions,
                encoder,
                checkpoint_path,
            )
        temporary.replace(destination)
        statistics = {key: totals[key] for key in COUNTS}
        statistics.update(
            {key: _ratio(totals[n], totals[d]) for key, (n, d) in RATIOS.items()}
        )
        statistics.update(
            {name: dict(counts) for name, counts in distributions.items()}
        )
        return NativeCalculatorOutput(
            statistics=statistics,
            status="OK" if totals["evaluable_record_count"] else "MISSING",
            status_detail={
                "reason": "EVALUATED_SUPPORTED_RULES"
                if totals["evaluable_record_count"]
                else "NO_EVALUABLE_RECORDS",
                "rule_scope": sorted(set(ATTRIBUTES.values()) | {"DEONTIC"}),
                "scope": "explicit_within_body_constraints",
                "recall_mode": parameters.get("mode", "rule_only"),
                "encoder": encoder.identity if encoder else None,
                "embedding_cache_hits": encoder.hits if encoder else 0,
                "embedding_cache_misses": encoder.misses if encoder else 0,
                "checkpoint_records_reused": resumed,
            },
            decisions=EvidenceOutput(
                path=destination,
                kind="CALCULATOR_DECISIONS",
                schema_ref=DECISION_SCHEMA_REF,
                record_count=totals["input_count"],
                contains_raw_content=True,
                redaction_policy="limited-original-excerpts-and-structured-fields-unredacted",
            ),
        )

    @staticmethod
    def _stream(
        context,
        parameters,
        destination,
        totals,
        distributions,
        encoder,
        checkpoint_path,
    ):
        resumed = 0
        persistent = checkpoint_path != ":memory:"
        with (
            closing(sqlite3.connect(checkpoint_path, timeout=30)) as checkpoint,
            context.input_path.open("rb") as source,
            destination.open("w", encoding="utf-8") as output,
        ):
            checkpoint.execute(
                "CREATE TABLE IF NOT EXISTS decisions (line INTEGER PRIMARY KEY, fingerprint TEXT, row TEXT)"
            )
            pending = []
            for line, raw in enumerate(source, 1):
                if not raw.strip():
                    continue
                fingerprint = hashlib.sha256(raw).hexdigest() if persistent else None
                cached = (
                    checkpoint.execute(
                        "SELECT row FROM decisions WHERE line=? AND fingerprint=?",
                        (line, fingerprint),
                    ).fetchone()
                    if persistent
                    else None
                )
                if cached:
                    [serialized] = cached
                    row = json.loads(serialized)
                    resumed += 1
                else:
                    try:
                        record = SampleRecordV2.model_validate_json(raw)
                    except ValueError as exc:
                        raise CalculatorValidationError(
                            "invalid normalized JSONL record", line=line
                        ) from exc
                    row = evaluate(record, parameters, encoder)
                    serialized = json.dumps(
                        row, ensure_ascii=False, separators=(",", ":")
                    )
                    if persistent:
                        pending.append((line, fingerprint, serialized))
                _count(totals, distributions, row)
                output.write(serialized + "\n")
                if len(pending) == 64:
                    BodyContentConsistencyCalculator._commit(checkpoint, pending)
                if context.progress and totals["input_count"] % 100 == 0:
                    context.progress(
                        totals["input_count"],
                        {"evaluable": totals["evaluable_record_count"]},
                    )
            BodyContentConsistencyCalculator._commit(checkpoint, pending)
        return resumed

    @staticmethod
    def _commit(checkpoint, pending):
        if pending:
            with checkpoint:
                checkpoint.executemany(
                    "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)", pending
                )
            pending.clear()

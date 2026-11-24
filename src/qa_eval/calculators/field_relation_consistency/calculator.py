"""Stream 0207 field-relation decisions through the existing DIRECT protocol."""

import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_eval.calculators.field_relation_consistency import (
    configuration,
    engine,
    legacy,
    local_matching,
    structured,
)
from qa_eval.calculators.protocol import (
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)
from qa_eval.contracts.datasets import SampleRecordV2

KEY = "field_relation_consistency"
VERSION = "2.0.0"
DECISIONS = "field-relation-decisions.jsonl"
EVIDENCE_KIND = "FIELD_RELATION_CONSISTENCY_DECISIONS"
MATCHERS = {
    "exact_match": structured.exact_match,
    "structured_match": structured.structured_match,
    "semantic_match": local_matching.semantic_match,
    "type_match": local_matching.type_match,
}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def statistics(labels, exclusions, reasons, outcomes, conflict_count):
    evaluable = labels["1"] + labels["0"]
    total = evaluable + labels["NOT_EVALUATED"]
    return {
        "input_count": total,
        "consistent_record_count": labels["1"],
        "inconsistent_record_count": labels["0"],
        "not_evaluated_record_count": labels["NOT_EVALUATED"],
        "evaluable_record_count": evaluable,
        "confirmed_conflict_count": conflict_count,
        "consistent_among_evaluable": ratio(labels["1"], evaluable),
        "evaluable_record_ratio": ratio(evaluable, total),
        "confirmed_consistent_record_ratio": ratio(labels["1"], total),
        "exclusions": dict(sorted(exclusions.items())),
        "relation_outcomes": dict(sorted(outcomes.items())),
        "relation_unknown_reasons": dict(sorted(reasons.items())),
    }


class FieldRelationConsistencyCalculator:
    calculator_key = KEY
    version = VERSION

    def source_paths(self):
        return (
            Path(__file__),
            Path(configuration.__file__),
            Path(engine.__file__),
            Path(legacy.__file__),
            Path(local_matching.__file__),
            Path(structured.__file__),
            configuration.SCHEMA_PATH,
        )

    def validate(self, context, parameters):
        configuration.settings(parameters)
        return []

    def execute(self, context, parameters):
        options = configuration.settings(parameters)
        adapter = legacy.LegacyAdapter(options["fields"])
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISIONS
        input_path = context.input_artifacts.get("NORMALIZED_DATA", context.input_path)
        labels, exclusions, reasons, outcomes = (
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
        conflict_count = 0
        with (
            TemporaryDirectory(dir=context.output_dir) as root,
            closing(sqlite3.connect(Path(root) / "sample-ids.sqlite3")) as ids,
            input_path.open("rb") as source,
            destination.open("w", encoding="utf-8") as output,
        ):
            ids.execute("CREATE TABLE samples (id TEXT PRIMARY KEY)")
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                record = SampleRecordV2.model_validate_json(line)
                if record.dataset_version_id != context.dataset_version_id:
                    raise CalculatorValidationError(
                        "normalized sample belongs to another dataset version",
                        line=line_number,
                    )
                if not ids.execute(
                    "INSERT OR IGNORE INTO samples VALUES (?)", (record.sample_id,)
                ).rowcount:
                    raise CalculatorValidationError(
                        "duplicate normalized sample ID", line=line_number
                    )
                result = engine.evaluate(adapter.extract(record), options, MATCHERS)
                label, reason = result.pop("label"), result.pop("reason")
                labels[label] += 1
                if label == "NOT_EVALUATED":
                    exclusions[reason] += 1
                for relation in result["relations"]:
                    outcomes[f"{relation['id']}/{relation['status']}"] += 1
                    if relation["status"] == "UNKNOWN":
                        reasons[f"{relation['id']}/{relation['reason']}"] += 1
                    conflict_count += relation["confirmed_conflict"]
                decision = CalculatorDecisionV1(
                    row_id=record.sample_id,
                    sample_id=record.sample_id,
                    rule_id="field_relation_consistency_v2",
                    status=label,
                    target="sample",
                    reason=reason,
                    evidence_refs=[
                        {"artifact_key": DECISIONS, "row_id": record.sample_id}
                    ],
                    attributes=result | {"normalized_line_number": line_number},
                )
                output.write(decision.model_dump_json() + "\n")
                if context.progress and line_number % 1000 == 0:
                    context.progress(sum(labels.values()), {})
        values = statistics(labels, exclusions, reasons, outcomes, conflict_count)
        count = values["input_count"]
        status = "OK" if values["evaluable_record_count"] else "MISSING"
        if count == exclusions["NO_APPLICABLE_RELATION"]:
            status = "NOT_APPLICABLE"
        return NativeCalculatorOutput(
            values,
            status=status,
            status_detail={
                "business_calibration_status": "PENDING",
                "coverage_scope": "FULL_INPUT_ARTIFACT",
                "aggregation": {"min_coverage": 0.6, "pass_threshold": 0.8}
                | options.get("aggregation", {}),
            },
            decisions=EvidenceOutput(
                destination,
                EVIDENCE_KIND,
                DECISION_SCHEMA_REF,
                count,
                redaction_policy="identifiers-positions-and-value-hashes-no-excerpts",
                attributes={"business_calibration_status": "PENDING"},
            ),
        )

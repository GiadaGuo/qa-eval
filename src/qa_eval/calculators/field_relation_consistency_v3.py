"""Stream v3 field relations using the frozen rule engine."""

from collections import Counter
from pathlib import Path

from qa_eval.calculators import resolved_consistency_fields
from qa_eval.calculators.field_relation_consistency import configuration, engine
from qa_eval.calculators.field_relation_consistency.calculator import (
    DECISIONS,
    EVIDENCE_KIND,
    MATCHERS,
    FieldRelationConsistencyCalculator,
    statistics,
)
from qa_eval.calculators.protocol import (
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.calculators.resolved_consistency_fields import field_views
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)


def resolved_settings(parameters):
    # The shared rule validator expects legacy selector identities. For v3
    # fields that only declare a type, use the field alias for that check.
    fields = {
        name: spec
        if "selector" in spec
        else {
            **spec,
            "selector": "record_pointer",
            "path": f"/metadata/{name}",
        }
        for name, spec in parameters["fields"].items()
    }
    return configuration.settings({**parameters, "fields": fields})


class LogicalFieldRelationConsistencyCalculator(FieldRelationConsistencyCalculator):
    version = "2.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(resolved_consistency_fields.__file__),
        )

    def validate(self, context, parameters):
        resolved_settings(parameters)
        return []

    def execute(self, context, parameters):
        options = resolved_settings(parameters)
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("field relations require a v3 Resolver")
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISIONS
        labels, exclusions, reasons, outcomes = (
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
        conflict_count = 0
        with destination.open("w", encoding="utf-8") as output:
            for line_number, record in enumerate(context.resolved_records(), 1):
                sample_id = str(record.sample_id)
                fields = field_views(
                    record, options["fields"], context.dataset_version_id
                )
                result = engine.evaluate(fields, options, MATCHERS)
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
                    row_id=sample_id,
                    sample_id=sample_id,
                    rule_id="field_relation_consistency_v2",
                    status=label,
                    target="sample",
                    reason=reason,
                    evidence_refs=[{"artifact_key": DECISIONS, "row_id": sample_id}],
                    attributes=result,
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

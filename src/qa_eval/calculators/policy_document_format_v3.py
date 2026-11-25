from collections import Counter
from pathlib import Path

from qa_eval.calculators import policy_resolved_fields
from qa_eval.calculators.policy_document_format.calculator import (
    ARTIFACT,
    PolicyDocumentFormatCalculator,
    analyze,
    settings,
    statistics,
)
from qa_eval.calculators.policy_resolved_fields import (
    field,
    origins,
    policy_record,
    require_resolver,
)
from qa_eval.calculators.protocol import (
    CalculatorContext,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF


class LogicalPolicyDocumentFormatCalculator(PolicyDocumentFormatCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(policy_resolved_fields.__file__),
        )

    def validate(self, context, parameters):
        require_resolver(context)
        return super().validate(context, parameters)

    def execute(self, context: CalculatorContext, parameters):
        options = settings(parameters)
        require_resolver(context)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / ARTIFACT
        counts, types, reasons, signals = Counter(), Counter(), Counter(), Counter()
        with destination.open("w", encoding="utf-8") as output:
            for resolved in context.resolved_records():
                extra = {
                    key: field(resolved, key)
                    for key in ("type", "policyType", "releaseunit")
                }
                record = policy_record(
                    resolved,
                    {
                        "extra": extra,
                        "document_type": field(resolved, "document_type"),
                        "document_number": {
                            key: field(resolved, key) for key in ("filenumber", "issue")
                        },
                        "publisher": {
                            key: field(resolved, key)
                            for key in ("organization", "publishunit")
                        },
                        "attachments": {"url": field(resolved, "attachurl")},
                    },
                    html_attributes={
                        key: field(resolved, key)
                        for key in (
                            "format_original_verified",
                            "format_standard_scope_verified",
                        )
                    },
                )
                decision = analyze(record, options)
                decision.attributes["source_origins"] = origins(resolved)
                attributes = decision.attributes
                counts["input"] += 1
                counts[decision.status] += 1
                counts["violations"] += bool(attributes["normative_violation"])
                types[attributes["doc_type"] or "UNKNOWN"] += 1
                reasons.update(attributes["reasons"])
                for part in attributes["segments"]:
                    for rule in part["rules"]:
                        signals[f"{rule['id']}/{rule['status']}"] += 1
                        if rule["status"] == "UNKNOWN":
                            reasons[f"{rule['id']}/{rule['reason']}"] += 1
                output.write(decision.model_dump_json() + "\n")
                if context.progress and counts["input"] % 1000 == 0:
                    context.progress(counts["input"], {})
        return NativeCalculatorOutput(
            statistics(counts, types, reasons, signals),
            status="OK" if counts["input"] else "NOT_APPLICABLE",
            status_detail={"thresholds_pending_calibration": True, "settings": options},
            decisions=EvidenceOutput(
                destination,
                "POLICY_DOCUMENT_FORMAT_DECISIONS",
                DECISION_SCHEMA_REF,
                counts["input"],
                redaction_policy="identifiers-offsets-rule-states-no-excerpts",
            ),
        )

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from qa_eval.calculators import policy_resolved_fields
from qa_eval.calculators.policy_effectiveness.calculator import (
    DECISION_ARTIFACT,
    PolicyEffectivenessCalculator,
    _event,
    _statistics,
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
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF


class LogicalPolicyEffectivenessCalculator(PolicyEffectivenessCalculator):
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

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> NativeCalculatorOutput:
        reference, observed, _ = self._settings(context, parameters)
        require_resolver(context)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISION_ARTIFACT
        counts = Counter()
        with destination.open("w", encoding="utf-8") as output:
            for resolved in context.resolved_records():
                record = policy_record(
                    resolved,
                    {
                        "document_type": field(resolved, "document_type"),
                        "temporal": {
                            key: {"value": field(resolved, key)}
                            for key in ("formatted_at", "published_at")
                        },
                    },
                )
                record.source = SimpleNamespace(
                    source_record_id=None,
                    source_row=None,
                    source_artifact_id=None,
                )
                event = _event(record, reference, observed, "document_type")
                for key in ("source_record_id", "source_row", "source_artifact_id"):
                    event.attributes.pop(key)
                event.attributes["source_origins"] = origins(resolved)
                counts["input"] += 1
                counts[event.attributes["applicability"]] += 1
                if event.attributes["effectiveness"] is not None:
                    counts[event.status] += 1
                output.write(event.model_dump_json() + "\n")
                if context.progress and counts["input"] % 1000 == 0:
                    context.progress(counts["input"], {})
        detail = {
            "reference_date": reference.isoformat(),
            "observation_date": observed.isoformat(),
            "observation_date_source": "EXPLICIT_REVIEW"
            if "observation_date" in parameters
            else "MANIFEST_CAPTURE",
            "document_type_source": "SELECTED_SLOT",
            "duplicate_input_rows": 0,
        }
        return NativeCalculatorOutput(
            statistics=_statistics(counts),
            status="OK" if counts["APPLICABLE"] else "NOT_APPLICABLE",
            status_detail=detail,
            decisions=EvidenceOutput(
                destination,
                "POLICY_EFFECTIVENESS_DECISIONS",
                DECISION_SCHEMA_REF,
                counts["input"],
                attributes={
                    "offset_unit": "UNICODE_CODE_POINT",
                    "interval": "ZERO_BASED_HALF_OPEN",
                },
            ),
        )

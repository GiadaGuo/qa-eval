"""Run the original repetition scanners over the v3 field stream."""

from pathlib import Path

from qa_eval.calculators import resolved_consistency_fields
from qa_eval.calculators.protocol import CalculatorValidationError
from qa_eval.calculators.repetition import (
    CrossRecordDuplicateCalculator,
    IntraRecordRepetitionCalculator,
)
from qa_eval.calculators.resolved_consistency_fields import field_specs, field_views


class ResolvedRepetition:
    version = "1.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(resolved_consistency_fields.__file__),
        )

    def validate(self, context, parameters):
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("repetition requires a v3 Resolver")
        return []

    def rows(self, context, parameters):
        specs = field_specs(self.calculator_key, parameters)
        for record in context.resolved_records():
            views = field_views(record, specs, context.dataset_version_id)
            yield (
                str(record.sample_id),
                [
                    (
                        view.value if view.presence == "PRESENT" else None,
                        view.source_ref,
                    )
                    for view in views.values()
                ],
            )


class LogicalCrossRecordDuplicateCalculator(
    ResolvedRepetition, CrossRecordDuplicateCalculator
):
    pass


class LogicalIntraRecordRepetitionCalculator(
    ResolvedRepetition, IntraRecordRepetitionCalculator
):
    pass

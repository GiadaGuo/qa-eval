"""Version the existing text-quality scorers for Resolver-backed inference."""

from pathlib import Path

from qa_eval.calculators.semantic_inference import (
    InformationMissingDegreeCalculator,
    KnowledgeInformationDensityCalculator,
    TextCoherenceCalculator,
)


class ResolvedTextQuality:
    version = "1.1.0"

    def source_paths(self):
        return (*super().source_paths(), Path(__file__))


class LogicalInformationMissingDegreeCalculator(
    ResolvedTextQuality, InformationMissingDegreeCalculator
):
    pass


class LogicalKnowledgeInformationDensityCalculator(
    ResolvedTextQuality, KnowledgeInformationDensityCalculator
):
    pass


class LogicalTextCoherenceCalculator(ResolvedTextQuality, TextCoherenceCalculator):
    pass

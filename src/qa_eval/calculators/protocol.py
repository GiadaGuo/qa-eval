from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from qa_eval.contracts.base import JsonObject


class CalculatorError(RuntimeError):
    """A structured failure that can safely cross the StageRun boundary."""

    code = "CALCULATOR_ERROR"

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.details = details


class CalculatorValidationError(CalculatorError, ValueError):
    code = "CALCULATOR_VALIDATION_ERROR"


class CalculatorLoadError(CalculatorError):
    code = "CALCULATOR_LOAD_ERROR"


@dataclass(frozen=True)
class CalculatorContext:
    run_id: UUID
    stage_run_id: UUID
    dataset_version_id: UUID
    metric_key: str
    metric_version: str
    input_path: Path
    output_dir: Path
    input_schema_ref: str


@dataclass(frozen=True)
class EvidenceOutput:
    path: Path
    kind: str
    schema_ref: str
    record_count: int
    subject_scope: str = "SAMPLE"
    contains_raw_content: bool = False
    redaction_policy: str = "hashes-and-identifiers-only"
    error_count: int = 0
    attributes: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class CalculatorOutput:
    observations: list[JsonObject]
    population: JsonObject
    semantic_coverage: JsonObject
    evidence: list[EvidenceOutput] = field(default_factory=list)
    diagnostics: list[JsonObject] = field(default_factory=list)


@runtime_checkable
class Calculator(Protocol):
    calculator_key: str
    version: str

    def source_paths(self) -> tuple[Path, ...]: ...

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]: ...

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput: ...


@runtime_checkable
class SharedInferenceCalculator(Protocol):
    """Scores one metric from a shared, record-level inference output."""

    calculator_key: str
    version: str

    def source_paths(self) -> tuple[Path, ...]: ...

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]: ...

    def score_shared(
        self,
        context: CalculatorContext,
        parameters: JsonObject,
        records: list[JsonObject],
    ) -> CalculatorOutput: ...

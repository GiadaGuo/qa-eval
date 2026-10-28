from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import re
from pathlib import Path

from qa_eval.calculators.protocol import (
    Calculator,
    CalculatorLoadError,
)
from qa_eval.contracts.registry import CalculatorDescriptorV1

TRUSTED_MODULE_PREFIXES = ("qa_eval.calculators.",)
CALCULATOR_PACKAGE_ROOT = Path(__file__).resolve().parent


class TrustedCalculatorLoader:
    def load(self, descriptor: CalculatorDescriptorV1) -> Calculator:
        if descriptor.availability.status != "AVAILABLE":
            raise CalculatorLoadError(
                "Calculator is not available for execution",
                status=descriptor.availability.status,
                reason=descriptor.availability.reason,
            )
        missing_dependencies = [
            dependency
            for dependency in descriptor.runtime.dependencies
            if importlib.util.find_spec(self._module_name(dependency)) is None
        ]
        if missing_dependencies:
            raise CalculatorLoadError(
                "Calculator runtime dependencies are unavailable",
                dependencies=missing_dependencies,
            )
        module_name, separator, attribute_name = descriptor.entrypoint.partition(":")
        if not separator or not module_name or not attribute_name:
            raise CalculatorLoadError(
                "Calculator entrypoint must use 'module:attribute' syntax",
                entrypoint=descriptor.entrypoint,
            )
        if not module_name.startswith(TRUSTED_MODULE_PREFIXES):
            raise CalculatorLoadError(
                "Calculator entrypoint is outside the trusted package",
                entrypoint=descriptor.entrypoint,
            )
        try:
            module = importlib.import_module(module_name)
            implementation = getattr(module, attribute_name)
            calculator = (
                implementation() if inspect.isclass(implementation) else implementation
            )
        except (ImportError, AttributeError, TypeError) as exc:
            raise CalculatorLoadError(
                "Calculator entrypoint cannot be loaded",
                entrypoint=descriptor.entrypoint,
                cause=type(exc).__name__,
            ) from exc
        if not isinstance(calculator, Calculator):
            raise CalculatorLoadError(
                "Calculator implementation does not satisfy the platform Protocol",
                entrypoint=descriptor.entrypoint,
            )
        if (
            calculator.calculator_key != descriptor.calculator_key
            or calculator.version != descriptor.version
        ):
            raise CalculatorLoadError(
                "Calculator identity does not match its Registry descriptor",
                expected_key=descriptor.calculator_key,
                expected_version=descriptor.version,
                actual_key=calculator.calculator_key,
                actual_version=calculator.version,
            )
        actual_digest = self.code_digest(calculator)
        if actual_digest != descriptor.code_digest:
            raise CalculatorLoadError(
                "Calculator code digest does not match its Registry descriptor",
                expected=descriptor.code_digest,
                actual=actual_digest,
            )
        return calculator

    @staticmethod
    def _module_name(requirement: str) -> str:
        distribution = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip()
        return distribution.replace("-", "_")

    @staticmethod
    def code_digest(calculator: Calculator) -> str:
        digest = hashlib.sha256()
        paths = tuple(calculator.source_paths())
        if not paths:
            raise CalculatorLoadError("Calculator declares no source files")
        for path in sorted((item.resolve() for item in paths), key=str):
            if not path.is_relative_to(CALCULATOR_PACKAGE_ROOT):
                raise CalculatorLoadError(
                    "Calculator source file is outside the trusted package",
                    path=str(path),
                )
            if not path.is_file():
                raise CalculatorLoadError(
                    "Calculator source file is unavailable", path=str(path)
                )
            relative = path.relative_to(CALCULATOR_PACKAGE_ROOT).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return f"sha256:{digest.hexdigest()}"

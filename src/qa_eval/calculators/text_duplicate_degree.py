"""Platform entrypoint for the validated 02080103 Calculator implementation."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

_IMPLEMENTATION = (
    Path(__file__).resolve().parents[3]
    / ".agents/skills/dataset-data-quality-evaluator/scripts/calculate_text_duplicates.py"
)
_EXPECTED_DIGEST = "e8cf6b6cf82996e06d2cecea4e30d5eec6e8776c6e4175a3c21645d6d43a6d97"
_ACTUAL_DIGEST = hashlib.sha256(_IMPLEMENTATION.read_bytes()).hexdigest()
if _ACTUAL_DIGEST != _EXPECTED_DIGEST:
    raise RuntimeError(
        "Calculator implementation digest does not match Registry descriptor"
    )
_SPEC = importlib.util.spec_from_file_location(
    "qa_eval_02080103_calculator_v2", _IMPLEMENTATION
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Calculator implementation cannot be loaded: {_IMPLEMENTATION}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

calculate = _MODULE.calculate

__all__ = ["calculate"]

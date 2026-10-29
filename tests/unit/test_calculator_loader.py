from __future__ import annotations

from pathlib import Path

import pytest

from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorLoadError
from qa_eval.contracts.registry import CalculatorDescriptorV1

DESCRIPTOR_PATH = (
    Path(__file__).parents[2]
    / "configs/registry/calculators/text-duplicate-degree-v2.0.0.json"
)


def _descriptor(**updates) -> CalculatorDescriptorV1:
    value = CalculatorDescriptorV1.model_validate_json(DESCRIPTOR_PATH.read_text())
    return value.model_copy(update=updates)


def test_loads_registered_platform_calculator_and_verifies_digest() -> None:
    calculator = TrustedCalculatorLoader().load(_descriptor())

    assert calculator.calculator_key == "text_duplicate_degree"
    assert calculator.version == "2.0.0"


@pytest.mark.parametrize(
    "filename, key, version",
    [
        ("text-perplexity-v1.0.0.json", "text_perplexity", "1.0.0"),
        ("text-purity-v1.0.0.json", "text_purity", "1.0.0"),
    ],
)
def test_loads_new_text_metric_calculators_with_fresh_digests(
    filename: str, key: str, version: str
) -> None:
    """Registry 描述符中的 code_digest 必须与实现文件当前内容一致。"""

    descriptor = CalculatorDescriptorV1.model_validate_json(
        (DESCRIPTOR_PATH.parent / filename).read_text()
    )
    calculator = TrustedCalculatorLoader().load(descriptor)

    assert calculator.calculator_key == key
    assert calculator.version == version


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"entrypoint": "os:path"}, "outside the trusted package"),
        ({"entrypoint": "qa_eval.calculators.missing:Calculator"}, "cannot be loaded"),
        ({"code_digest": f"sha256:{'0' * 64}"}, "code digest does not match"),
    ],
)
def test_rejects_untrusted_missing_or_digest_mismatched_entrypoint(
    updates: dict, message: str
) -> None:
    with pytest.raises(CalculatorLoadError, match=message):
        TrustedCalculatorLoader().load(_descriptor(**updates))


def test_rejects_missing_runtime_dependency() -> None:
    descriptor = _descriptor()
    runtime = descriptor.runtime.model_copy(
        update={"dependencies": ["qa-eval-definitely-missing>=1"]}
    )

    with pytest.raises(CalculatorLoadError, match="dependencies are unavailable"):
        TrustedCalculatorLoader().load(
            descriptor.model_copy(update={"runtime": runtime})
        )

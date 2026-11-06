from pathlib import Path

from qa_eval.application.services.applicability_service import (
    ApplicabilityService,
    DatasetApplicabilityContext,
)
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)

ROOT = Path(__file__).resolve().parents[2]


class AvailableLoader:
    def load(self, descriptor):
        return object()


def _metric(key="02080103"):
    return MetricDefinitionV1.model_validate_json(
        (ROOT / f"configs/registry/metrics/{key}-v1.0.0.json").read_text()
    )


def _calculator(key="exact-text-duplicate", version="1.0.0"):
    return CalculatorDescriptorV1.model_validate_json(
        (ROOT / f"configs/registry/calculators/{key}-v{version}.json").read_text()
    )


def _binding(enabled=True):
    return MetricCalculatorBindingV1.model_validate(
        {
            "metric_key": "02080103",
            "metric_version": "1.0.0",
            "calculator_key": "exact_text_duplicate",
            "calculator_version": "1.0.0",
            "priority": 100,
            "enabled": enabled,
        }
    )


def _context(*, modalities=None, roles=None, artifacts=None):
    return DatasetApplicabilityContext(
        modalities=set(modalities or ["text"]),
        content_roles=set(roles or ["article.body.plain"]),
        artifact_kinds=set(artifacts or ["NORMALIZED_DATA"]),
        has_profile=True,
    )


def test_applicability_exposes_four_distinct_states():
    service = ApplicabilityService(loader=AvailableLoader())
    metric = _metric()
    calculator = _calculator()

    applicable = service.evaluate(
        _context(),
        metric,
        calculator,
        _binding(),
        {"content_role": "article.body.plain"},
    )
    not_applicable = service.evaluate(
        _context(modalities=["image"]),
        metric,
        calculator,
        _binding(),
        {"content_role": "article.body.plain"},
    )
    needs_input = service.evaluate(
        _context(roles=["other"]),
        metric,
        calculator,
        _binding(),
        {"content_role": "article.body.plain"},
    )
    unavailable = service.evaluate(
        _context(),
        metric,
        calculator,
        _binding(enabled=False),
        {"content_role": "article.body.plain"},
    )

    assert applicable.status == "APPLICABLE"
    assert not_applicable.status == "NOT_APPLICABLE"
    assert not_applicable.findings[0].code == "MODALITY_MISMATCH"
    assert needs_input.status == "NEEDS_INPUT"
    assert needs_input.findings[0].code == "MISSING_CONTENT_ROLE"
    assert unavailable.status == "UNAVAILABLE"
    assert unavailable.findings[0].code == "BINDING_DISABLED"


def test_structural_completeness_requires_explicit_selector():
    service = ApplicabilityService(loader=AvailableLoader())
    metric = _metric("0204")
    calculator = _calculator("structural-completeness")
    binding = MetricCalculatorBindingV1.model_validate(
        {
            "metric_key": "0204",
            "metric_version": "1.0.0",
            "calculator_key": "structural_completeness",
            "calculator_version": "1.0.0",
            "priority": 100,
            "enabled": True,
        }
    )

    decision = service.evaluate(_context(), metric, calculator, binding, {})

    assert decision.status == "NEEDS_INPUT"
    assert decision.findings[0].code == "MISSING_SELECTOR_PARAMETER"


def test_policy_structure_requires_at_least_one_supported_body_role():
    service = ApplicabilityService(loader=AvailableLoader())
    metric = MetricDefinitionV1.model_validate_json(
        (ROOT / "configs/registry/metrics/0204-v2.0.0.json").read_text()
    )
    calculator = _calculator("policy-structure-compliance")
    binding = MetricCalculatorBindingV1.model_validate_json(
        (
            ROOT
            / "configs/registry/bindings/0204-v2-policy-structure-compliance.json"
        ).read_text()
    )

    missing = service.evaluate(
        _context(roles=["article.title"]), metric, calculator, binding, {}
    )
    present = service.evaluate(
        _context(roles=["article.body.html"]), metric, calculator, binding, {}
    )

    assert missing.status == "NEEDS_INPUT"
    assert missing.findings[0].code == "MISSING_ANY_CONTENT_ROLE"
    assert present.status == "APPLICABLE"

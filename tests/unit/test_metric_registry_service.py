import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.applicability_service import (
    ApplicabilityService,
    DatasetApplicabilityContext,
)
from qa_eval.application.services.metric_registry_service import (
    REGISTRY_ROOT,
    MetricRegistryService,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorLoadError
from qa_eval.contracts.registry import CalculatorAvailability, CalculatorDescriptorV1
from qa_eval.infrastructure.db.base import Base

CHECKLIST_METRIC_KEYS = {
    "0201", "0202", "0203", "0204", "0205", "0206", "0207", "0208",
    "02080101", "02080102", "02080103", "02080104", "02080105", "02080106",
    "02080107",
}


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_seed_and_query_registry() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        service.seed()
        service.seed()

        metric = service.get_metric("02080103", "1.0.0")
        calculator = service.get_calculator("text_duplicate_degree", "2.0.0")
        bindings = service.list_bindings("02080103")
        completeness = service.get_metric("0204", "1.0.0")
        exact = service.get_calculator("exact_text_duplicate", "1.0.0")

        assert metric is not None
        assert {item.key for item in metric.measurements} == {
            "exact_affected_record_ratio",
            "exact_redundant_record_ratio",
            "char_ngram_repetition_ratio",
            "word_ngram_repetition_ratio",
            "near_duplicate_candidate_record_count",
            "paragraph_affected_ratio",
        }
        assert calculator is not None
        assert calculator.input_contract.role_selectors == ["article.body.plain"]
        assert calculator.deterministic is True
        assert len(bindings) == 2
        assert all(item.enabled for item in bindings)
        assert completeness is not None
        assert exact is not None
        assert exact.semantic_coverage["covered"] == ["normalized exact duplicate"]


def test_checklist_metrics_are_fully_registered() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        counts = service.sync()

        assert {item.metric_key for item in service.list_metrics()} == (
            CHECKLIST_METRIC_KEYS
        )
        assert counts["metrics"] == len(CHECKLIST_METRIC_KEYS)
        # Every checklist metric carries at least one binding, so the registry
        # page can always resolve calculator information for it.
        for metric_key in CHECKLIST_METRIC_KEYS:
            bindings = service.list_bindings(metric_key)
            assert bindings, f"metric {metric_key} has no calculator binding"
            for binding in bindings:
                assert service.get_calculator(
                    binding.calculator_key, binding.calculator_version
                ) is not None


def test_placeholder_calculators_are_registered_but_not_executable() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        service.sync()

        placeholders = [
            descriptor
            for descriptor in service.list_calculators()
            if descriptor.availability.status != "AVAILABLE"
        ]
        assert len(placeholders) == 13
        for descriptor in placeholders:
            assert descriptor.entrypoint is None
            assert descriptor.code_digest is None
            assert descriptor.availability.reason
            assert descriptor.semantic_coverage["level"] == "NONE"
            with pytest.raises(CalculatorLoadError, match="not available"):
                TrustedCalculatorLoader().load(descriptor)


def test_placeholder_calculator_cannot_become_applicable() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        service.sync()

        metric = service.get_metric("0201", "1.0.0")
        calculator = service.get_calculator("format_compliance", "1.0.0")
        binding = service.list_bindings("0201")[0]
        assert metric is not None and calculator is not None

        decision = ApplicabilityService().evaluate(
            DatasetApplicabilityContext(
                modalities={"text"},
                content_roles={"article.body.plain"},
                artifact_kinds={"NORMALIZED_DATA"},
                has_profile=True,
            ),
            metric,
            calculator,
            binding,
            {},
        )

        assert decision.status == "UNAVAILABLE"
        assert decision.findings[0].code == "CALCULATOR_UNAVAILABLE"


def test_available_calculator_requires_entrypoint_and_digest() -> None:
    descriptor = CalculatorDescriptorV1.model_validate_json(
        (REGISTRY_ROOT / "calculators" / "format-compliance-v1.0.0.json").read_text()
    )
    available = descriptor.model_copy(
        update={"availability": CalculatorAvailability(status="AVAILABLE")}
    )

    with pytest.raises(ValueError, match="entrypoint and code_digest"):
        CalculatorDescriptorV1.model_validate(available.model_dump())

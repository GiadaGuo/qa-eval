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
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel

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
        assert placeholders == []


def test_text_metric_calculators_are_registered_and_executable() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        service.sync()

        perplexity = service.get_calculator("text_perplexity", "1.0.0")
        purity = service.get_calculator("text_purity", "1.0.0")
        assert perplexity is not None and purity is not None

        assert perplexity.availability.status == "AVAILABLE"
        assert perplexity.runtime.dependencies == ["kenlm"]
        assert perplexity.deterministic is True
        assert perplexity.semantic_coverage["level"] == "FULL"

        assert purity.availability.status == "AVAILABLE"
        assert purity.deterministic is True
        assert purity.semantic_coverage["level"] == "PARTIAL"
        assert purity.semantic_coverage["planned"] == [
            "隐性广告与软文的语义判断（由 Shared LLM Stage 补充后融合）"
        ]

        for descriptor in (perplexity, purity):
            loaded = TrustedCalculatorLoader().load(descriptor)
            assert loaded.calculator_key == descriptor.calculator_key
            assert loaded.version == descriptor.version

        # 指标定义的 limitations 不再标注「占位待实现」
        for metric_key in ("02080101", "02080106"):
            metric = service.get_metric(metric_key, "1.0.0")
            assert metric is not None
            assert not any("占位" in item for item in metric.definition.limitations)

        # 缺少 kenlm 依赖时 Loader 明确拒绝执行而不是静默降级
        runtime = perplexity.runtime.model_copy(
            update={"dependencies": ["kenlm-definitely-missing>=1"]}
        )
        with pytest.raises(CalculatorLoadError, match="dependencies are unavailable"):
            TrustedCalculatorLoader().load(
                perplexity.model_copy(update={"runtime": runtime})
            )


def test_format_compliance_calculator_is_applicable() -> None:
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

        assert decision.status == "APPLICABLE"
        assert decision.findings == []


def test_available_calculator_requires_entrypoint_and_digest() -> None:
    descriptor = CalculatorDescriptorV1.model_validate_json(
        (REGISTRY_ROOT / "calculators" / "format-compliance-v1.0.0.json").read_text()
    )
    available = descriptor.model_copy(
        update={
            "availability": CalculatorAvailability(status="AVAILABLE"),
            "entrypoint": None,
            "code_digest": None,
        }
    )

    with pytest.raises(ValueError, match="entrypoint and code_digest"):
        CalculatorDescriptorV1.model_validate(available.model_dump())


def test_invalid_legacy_shared_descriptor_is_isolated_and_sync_repairs_it() -> None:
    with _session() as session:
        service = MetricRegistryService(session)
        service.sync()
        row = session.query(CalculatorImplementationModel).filter_by(
            calculator_key="knowledge_information_density", version="1.0.0"
        ).one()
        execution = dict(row.document["execution"])
        for field in ("prompt_version", "rubric_version", "rubric_output_schema"):
            execution.pop(field)
        row.document = {**row.document, "execution": execution}
        session.flush()

        descriptors = service.list_calculators()
        validation = service.calculator_validation()
        readiness = service.runtime_readiness()

        assert "knowledge_information_density" not in {
            item.calculator_key for item in descriptors
        }
        assert validation["invalid"] == [
            {
                "calculator_key": "knowledge_information_density",
                "version": "1.0.0",
                "missing_fields": [
                    "execution.prompt_version",
                    "execution.rubric_version",
                    "execution.rubric_output_schema",
                ],
                "errors": [
                    {
                        "location": "execution",
                        "type": "value_error",
                        "message": (
                            "Value error, SHARED_INFERENCE execution requires "
                            "operator_type, prompt_family, prompt_version, rubric, "
                            "rubric_version, rubric_output_schema, output_field, and "
                            "aggregation_strategy"
                        ),
                    }
                ],
            }
        ]
        invalid_readiness = next(
            item
            for item in readiness["calculators"]
            if item["calculator_key"] == "knowledge_information_density"
        )
        assert invalid_readiness["ready"] is False
        assert invalid_readiness["error"]["code"] == "INVALID_REGISTRY_DESCRIPTOR"

        synced = service.sync()
        assert synced["repaired_calculators"] == [
            {
                "calculator_key": "knowledge_information_density",
                "version": "1.0.0",
                "missing_fields": [
                    "execution.prompt_version",
                    "execution.rubric_version",
                    "execution.rubric_output_schema",
                ],
            }
        ]
        assert service.calculator_validation()["invalid"] == []
        repaired = service.get_calculator(
            "knowledge_information_density", "1.0.0"
        )
        assert repaired is not None
        assert repaired.execution.prompt_version == "1.1.0"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.base import Base


def test_seed_and_query_registry() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = MetricRegistryService(session)
        service.seed()
        service.seed()

        metric = service.get_metric("02080103", "1.0.0")
        calculator = service.get_calculator("text_duplicate_degree", "2.0.0")
        bindings = service.list_bindings("02080103")

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
        assert len(bindings) == 1
        assert bindings[0].enabled is True

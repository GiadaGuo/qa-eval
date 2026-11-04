from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qa_eval.application.evaluation.operational_scoring import (
    build_operational_scoring_report,
    load_scoring_profile,
    map_metric_score,
)
from qa_eval.application.services.evaluation_recommendation_service import (
    MEASUREMENT_POLICY_HINTS,
)
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityReportV1_1
from qa_eval.contracts.scoring import MetricScoreContribution


def _result(key: str, measurement: str, value, *, status: str = "OK") -> MetricResultV1:
    stage_run_id = uuid4()
    payload = {
        "metric_result_id": uuid4(),
        "run_id": uuid4(),
        "stage_run_id": stage_run_id,
        "dataset_version_id": uuid4(),
        "metric": {"key": key, "version": "1.0.0"},
        "subject": {"scope": "DATASET", "id": "dataset"},
        "status": status,
        "observations": [{"key": measurement, "value": value, "value_type": "NUMBER", "unit": "ratio"}],
        "population": {"input_count": 20, "evaluated_count": 20, "excluded_count": 0, "exclusions": []},
        "created_at": datetime.now(UTC),
    }
    if status == "PARTIAL":
        payload["semantic_coverage"] = {"level": "PARTIAL", "covered": ["sample"], "excluded": ["full population"], "limitations": []}
    return MetricResultV1.model_validate(payload)


def test_ratio_mappings_reject_missing_and_out_of_range_values():
    profile = load_scoring_profile()
    higher = next(item for item in profile.metrics if item.metric_id == "0201")
    lower = next(item for item in profile.metrics if item.metric_id == "02080103")

    assert map_metric_score(0.9978609625668449, higher) == (99.7861, None)
    assert map_metric_score(0.005961950696677385, lower) == (99.4038, None)
    assert map_metric_score(None, higher) == (None, "MEASUREMENT_MISSING")
    assert map_metric_score(1.01, higher) == (None, "MEASUREMENT_OUT_OF_RANGE")
    assert map_metric_score(-0.01, lower) == (None, "MEASUREMENT_OUT_OF_RANGE")


def test_operational_profile_keeps_ppl_and_observations_unscored():
    profile = load_scoring_profile()
    ppl = next(item for item in profile.metrics if item.metric_id == "02080101")
    authenticity = next(item for item in profile.metrics if item.metric_id == "0205")

    assert map_metric_score(83348325.71, ppl) == (None, "OBSERVE_ONLY")
    assert map_metric_score(0.0, authenticity) == (None, "OBSERVE_ONLY")
    assert MEASUREMENT_POLICY_HINTS["02080101"] == ("mean_perplexity", None)


def test_legacy_snapshot_restores_explicit_sampled_estimate_alias():
    contribution = MetricScoreContribution.model_validate(
        {
            "metric_id": "0202",
            "measurement_key": "safe_record_ratio",
            "raw_value": 1.0,
            "score": 100.0,
            "contribution_type": "SAMPLED_ESTIMATE",
            "weight": 1,
            "scoring_mapping_version": "ratio-higher-v1",
        }
    )
    assert contribution.sampled_estimate_score == 100.0


def test_0208_aggregation_separates_deterministic_and_sampled_estimates():
    profile = load_scoring_profile()
    run_id = uuid4()
    base = QualityReportV1_1.model_construct(
        report_id=uuid4(),
        run_id=run_id,
        dimension_scores=[],
    )
    results = [
        _result("02080101", "mean_perplexity", 83348325.71),
        _result("02080102", "mean_effective_knowledge_score", 0.884, status="PARTIAL"),
        _result("02080103", "exact_affected_record_ratio", 0.005961950696677385),
        _result("02080104", "incomplete_text_ratio", 0.012366310160427808),
        _result("02080105", "key_element_missing_ratio", 0.05, status="PARTIAL"),
        _result("02080106", "interference_record_ratio", 0.0036843515541264736),
        _result("02080107", "coherent_text_ratio", 1.0, status="PARTIAL"),
    ]
    stages = {
        item.stage_run_id: {"stats": {"source_records": 14960}}
        for item in results
    }

    report = build_operational_scoring_report(base, results, stages, profile)
    axis = report.radar[7]

    assert axis.estimate_type == "MIXED"
    assert axis.deterministic_score == pytest.approx(99.2663, abs=0.0001)
    assert axis.estimated_score == pytest.approx(96.8665, abs=0.0001)
    assert axis.deterministic_coverage == 0.5
    assert axis.estimated_coverage == 1
    assert axis.sample_size == 20
    assert axis.population_size == 14960
    assert {item.metric_id for item in axis.excluded_metrics} == {"02080101"}
    sampled = next(item for item in axis.contributing_metrics if item.metric_id == "02080102")
    assert sampled.sampled_estimate_score == 88.4
    assert sampled.coverage_ratio == pytest.approx(20 / 14960)
    assert sampled.sampling_method is None
    assert sampled.missing_sampling_metadata == ["sampling_method"]

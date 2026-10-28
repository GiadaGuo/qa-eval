# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 质量报告主测量拆分单测：契约绑定的 measurement 是唯一主结果，
# 其余观测字段是同一次执行的诊断输出，不得当作独立计算方法。
# ====================================================

from datetime import UTC, datetime
from uuid import uuid4

from qa_eval.application.evaluation.report_generator import (
    MetricCatalogEntry,
    build_quality_report,
)
from qa_eval.application.evaluation.rule_evaluator import evaluate_contract
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import (
    IssueIndexV1,
    QualityContractVersionV1,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)

OBSERVATIONS = [
    {"key": "exact_affected_record_ratio", "value": 0.006,
     "value_type": "NUMBER", "unit": "ratio"},
    {"key": "exact_redundant_record_ratio", "value": 0.003,
     "value_type": "NUMBER", "unit": "ratio"},
    {"key": "char_ngram_repetition_ratio", "value": 0.34,
     "value_type": "NUMBER", "unit": "ratio"},
    {"key": "word_ngram_repetition_ratio", "value": 0.507,
     "value_type": "NUMBER", "unit": "ratio"},
    {"key": "near_duplicate_candidate_record_count", "value": 7517,
     "value_type": "NUMBER", "unit": "record"},
    {"key": "paragraph_affected_ratio", "value": 0.006,
     "value_type": "NUMBER", "unit": "ratio"},
]


def _contract(measurement: str) -> QualityContractVersionV1:
    return QualityContractVersionV1.model_validate(
        {
            "schema_version": "1.0",
            "contract_id": uuid4(),
            "contract_version_id": uuid4(),
            "version_label": "1.0.0",
            "name": "user-contract",
            "status": "PUBLISHED",
            "applicability": {"modalities": ["text"], "task_types": []},
            "items": [
                {
                    "key": "duplicate-gate",
                    "metric": {
                        "key": "02080103",
                        "version": "1.0.0",
                        "measurement": measurement,
                    },
                    "scope": "DATASET",
                    "calculation": {},
                    "rule": {"operator": "LTE", "threshold": 0.9},
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                }
            ],
            "gate": {
                "unknown_policy": "FAIL",
                "fail_on_severities": ["ERROR", "CRITICAL"],
            },
            "fingerprint": f"sha256:{'a' * 64}",
            "published_at": NOW,
        }
    )


def _result(observations, status="OK") -> MetricResultV1:
    return MetricResultV1.model_validate(
        {
            "schema_version": "1.0",
            "metric_result_id": uuid4(),
            "run_id": uuid4(),
            "stage_run_id": uuid4(),
            "dataset_version_id": uuid4(),
            "metric": {"key": "02080103", "version": "1.0.0"},
            "subject": {"scope": "DATASET", "id": "dataset"},
            "status": status,
            "observations": observations,
            "evidence": [],
            "created_at": NOW,
        }
    )


def _index(result: MetricResultV1) -> IssueIndexV1:
    return IssueIndexV1.model_validate(
        {
            "issue_index_id": uuid4(),
            "run_id": result.run_id,
            "dataset_version_id": result.dataset_version_id,
            "artifact": {
                "artifact_id": uuid4(),
                "kind": "ISSUE_INDEX",
                "uri": "issue-index.jsonl",
                "format": "jsonl",
                "media_type": "application/x-ndjson",
                "schema_ref": "qa-eval://schemas/quality-issue/1.0.schema.json",
                "size_bytes": 0,
                "record_count": 0,
                "checksum": {"algorithm": "sha256", "value": "0" * 64},
            },
            "supported_filters": ["metric_key"],
            "summary": {"confirmed_count": 0, "candidate_count": 0},
        }
    )


def _catalog():
    return [
        MetricCatalogEntry(
            key="02080103", version="1.0.0", name="重复内容程度",
            dimension="DATA_QUALITY",
        )
    ]


def _build(contract, result, catalog=None):
    evaluations = evaluate_contract(
        contract, [result], id_factory=uuid4, evaluated_at=NOW
    )
    return build_quality_report(
        contract,
        [result],
        evaluations,
        [],
        {result.metric_result_id: _index(result)},
        _catalog() if catalog is None else catalog,
        execution_status="SUCCEEDED",
        id_factory=uuid4,
        generated_at=NOW,
    )


def test_report_promotes_contract_measurement_as_primary():
    contract = _contract("word_ngram_repetition_ratio")
    report = _build(contract, _result(OBSERVATIONS))

    assert len(report.metrics) == 1
    section = report.metrics[0]
    assert section.measurement is not None
    assert section.measurement.key == "word_ngram_repetition_ratio"
    assert section.measurement.value == 0.507
    # 诊断字段 = 其余 5 个观测，不含主测量
    assert [item.key for item in section.diagnostics] == [
        "exact_affected_record_ratio",
        "exact_redundant_record_ratio",
        "char_ngram_repetition_ratio",
        "near_duplicate_candidate_record_count",
        "paragraph_affected_ratio",
    ]
    # observations 保留全量 6 个，兼容既有消费者
    assert len(section.observations) == 6


def test_two_contracts_select_different_primary_measurements():
    result = _result(OBSERVATIONS)

    first = _build(_contract("word_ngram_repetition_ratio"), result)
    second = _build(_contract("exact_affected_record_ratio"), result)

    assert first.metrics[0].measurement.key == "word_ngram_repetition_ratio"
    assert second.metrics[0].measurement.key == "exact_affected_record_ratio"
    assert (
        second.metrics[0].measurement.key
        not in [item.key for item in second.metrics[0].diagnostics]
    )


def test_error_result_without_observations_has_no_primary_measurement():
    contract = _contract("word_ngram_repetition_ratio")
    result = _result([], status="ERROR")

    report = _build(contract, result)

    section = report.metrics[0]
    assert section.status == "ERROR"
    assert section.measurement is None
    assert section.diagnostics == []


def test_contract_measurement_missing_from_observations_is_none():
    contract = _contract("nonexistent_measurement")
    report = _build(contract, _result(OBSERVATIONS))

    section = report.metrics[0]
    assert section.measurement is None
    # 全部观测退化为诊断字段展示
    assert len(section.diagnostics) == 6


def test_comprehensive_scoring_separates_score_from_guide_coverage():
    catalog = [
        MetricCatalogEntry(
            key="0208", version="1.0.0", name="内容干净性",
            dimension="DATA_QUALITY", guide_status="UNAVAILABLE",
        ),
        MetricCatalogEntry(
            key="02080103", version="1.0.0", name="重复内容程度",
            dimension="DATA_QUALITY", parent_metric_key="0208",
            measurement_key="word_ngram_repetition_ratio",
            measurement_direction="LOWER_IS_BETTER",
            measurement_minimum=0, measurement_maximum=1,
            guide_status="APPLICABLE", guide_reason="selected",
        ),
        MetricCatalogEntry(
            key="02080104", version="1.0.0", name="文本完整性",
            dimension="DATA_QUALITY", parent_metric_key="0208",
            guide_status="UNAVAILABLE", guide_reason="calculator unavailable",
        ),
    ]

    report = _build(
        _contract("word_ngram_repetition_ratio"),
        _result(OBSERVATIONS),
        catalog,
    )

    assert report.schema_version == "1.1"
    assert report.scoring.evaluated_score == 49.3
    assert report.scoring.effective_coverage == 0.5
    assert report.scoring.included_metrics == ["02080103"]
    axis = next(item for item in report.radar if item.dimension == "0208")
    assert axis.score == 49.3
    assert axis.coverage == 0.5
    assert all(item.metric_key != "0208" for item in report.guide_coverage)
    assert report.recommendations == [
        "仍有 1 个指南叶子指标未形成可评分结果；补齐 Calculator、业务输入或评测计划后再提升覆盖率。"
    ]


def test_unscoreable_success_is_na_instead_of_zero():
    report = _build(_contract("word_ngram_repetition_ratio"), _result(OBSERVATIONS))

    assert report.scoring.evaluated_score is None
    assert report.scoring.effective_coverage == 0
    assert all(axis.score is None for axis in report.radar)

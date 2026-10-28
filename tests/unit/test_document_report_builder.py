# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 报告汇总器单测：分数程序复算、等权平均、任一 ERROR
# 总分置空、missing_items / recommendations 汇总口径。
# ====================================================

from datetime import UTC, datetime
from uuid import uuid4

from qa_eval.application.document_evaluation.evaluator import (
    EvaluationRunResult,
    MetricEvaluationOutcome,
)
from qa_eval.application.document_evaluation.report_builder import (
    SCORING_POLICY,
    build_report,
)
from qa_eval.domain.entities.document_version import DocumentVersion

RUBRIC = {
    "standard": {
        "name": "数据集说明文档质量评测标准",
        "version": "1.0.0",
    },
    "metrics": [
        {
            "metric_code": code,
            "metric_name": name,
            "items": [
                {
                    "item_code": f"{code}-0{index}",
                    "item_name": f"检查项{index}",
                }
                for index in range(1, 6)
            ],
        }
        for code, name in [
            ("0101", "基本信息完整性"),
            ("0102", "内容特征完整性"),
            ("0103", "建设过程完整性"),
            ("0104", "应用说明完整性"),
        ]
    ],
}


def _version() -> DocumentVersion:
    return DocumentVersion(
        id=uuid4(),
        dataset_id=uuid4(),
        version="v1",
        filename="readme.md",
        format="markdown",
        size_bytes=128,
        sha256="a" * 64,
        storage_uri="datasets/x/documents/y/readme.md",
    )


def _items(code: str, statuses: list[str]) -> list[dict]:
    return [
        {
            "item_code": f"{code}-0{index}",
            "status": status,
            "document_evidence": []
            if status == "MISSING"
            else [
                {
                    "line_start": 1,
                    "line_end": 1,
                    "excerpt": "# 数据集说明",
                }
            ],
            "inspected_scope": "检查全文",
            "expected_section": "六、技术支持" if status == "MISSING" else None,
            "reason": "判定依据",
            "suggestion": "修改建议",
            "fixability": "REQUIRES_HUMAN_INPUT",
        }
        for index, status in enumerate(statuses, start=1)
    ]


def _outcome(
    code: str, statuses: list[str] | None, error: str | None = None
) -> MetricEvaluationOutcome:
    return MetricEvaluationOutcome(
        metric_code=code,
        metric_name=next(
            m["metric_name"] for m in RUBRIC["metrics"] if m["metric_code"] == code
        ),
        status="ERROR" if error else "OK",
        items=[] if error else _items(code, statuses),
        error=error,
    )


def _build(outcomes) -> "object":
    return build_report(
        report_id=uuid4(),
        task_id=uuid4(),
        document_version=_version(),
        line_count=12,
        rubric=RUBRIC,
        rubric_sha256="b" * 64,
        result=EvaluationRunResult(outcomes=list(outcomes), limitations=[]),
        evaluator={
            "version": "1.0.0",
            "model": "mock-model",
            "prompt_version": "1.0.0",
            "parameters": {"temperature": 0.1},
        },
        generated_at=datetime.now(UTC),
    )


def test_score_is_recomputed_not_trusted():
    # 每指标 2/5 SATISFIED → 40 分；等权平均仍为 40
    report = _build(
        _outcome(
            code, ["SATISFIED", "SATISFIED", "INSUFFICIENT", "MISSING", "UNVERIFIABLE"]
        )
        for code in ("0101", "0102", "0103", "0104")
    )

    assert report.execution_status == "SUCCEEDED"
    for section in report.metrics:
        observations = {o.key: o for o in section.observations}
        assert observations["satisfied_count"].value == 2
        assert observations["required_count"].value == 5
        assert observations["score"].value == 40
    assert report.overall.score == 40
    assert report.overall.scoring_policy == SCORING_POLICY


def test_overall_is_equal_weight_mean():
    report = _build(
        [
            _outcome("0101", ["SATISFIED"] * 5),  # 100
            _outcome("0102", ["SATISFIED"] * 3 + ["INSUFFICIENT"] * 2),  # 60
            _outcome("0103", ["SATISFIED"] * 2 + ["UNVERIFIABLE"] * 3),  # 40
            _outcome("0104", ["MISSING"] * 5),  # 0
        ]
    )

    assert report.overall.score == 50.0
    # 总分恒等于指标 score 观测值的重新计算
    scores = [
        o.value for s in report.metrics for o in s.observations if o.key == "score"
    ]
    assert report.overall.score == round(sum(scores) / len(scores), 2)


def test_any_error_metric_nullifies_overall():
    report = _build(
        [
            _outcome("0101", ["SATISFIED"] * 5),
            _outcome("0102", ["SATISFIED"] * 5),
            _outcome("0103", ["SATISFIED"] * 5),
            _outcome("0104", None, error="校验重试耗尽"),
        ]
    )

    assert report.execution_status == "PARTIAL_SUCCEEDED"
    assert report.overall.score is None
    assert "0104" in report.overall.explanation
    error_section = next(s for s in report.metrics if s.metric.key == "0104")
    assert error_section.status == "ERROR"
    assert error_section.observations == []
    assert error_section.items == []


def test_all_error_metrics_marks_failed():
    report = _build(
        _outcome(code, None, error="LLM 网关传输失败")
        for code in ("0101", "0102", "0103", "0104")
    )

    assert report.execution_status == "FAILED"
    assert report.overall.score is None


def test_missing_items_and_recommendations_aggregation():
    report = _build(
        [
            _outcome(
                "0101", ["MISSING", "SATISFIED", "SATISFIED", "SATISFIED", "SATISFIED"]
            ),
            _outcome(
                "0102",
                ["SATISFIED", "INSUFFICIENT", "SATISFIED", "SATISFIED", "SATISFIED"],
            ),
            _outcome("0103", ["SATISFIED"] * 5),
            _outcome("0104", ["SATISFIED"] * 5),
        ]
    )

    assert [m.item_code for m in report.missing_items] == ["0101-01"]
    assert report.missing_items[0].expected_section == "六、技术支持"
    assert {r.item_code for r in report.recommendations} == {
        "0101-01",
        "0102-02",
    }

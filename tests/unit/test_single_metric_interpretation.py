from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from qa_eval.application.services import single_metric_interpretation as compact
from qa_eval.application.services.report_interpretation import ReportInterpretationJobs


@pytest.fixture
def facts():
    report = SimpleNamespace(gate_status="FAIL", issue_summary=SimpleNamespace(confirmed_count=0))
    result = SimpleNamespace(
        calculator_key="policy_document_format",
        statistics={
            "not_evaluated_count": 263479,
            "compliant_record_ratio": 0.1815,
            "unknown_reasons": {"TEMPLATE_PATH_UNCONFIRMED": 130093},
            "raw_content": "不应进入模型输入",
        },
    )
    return compact.build_facts(report, [result])


def point(**updates):
    return {"title": "门禁结果", "content": "本次门禁未通过。", "evidence_refs": ["gate"], **updates}


def output(**updates):
    return {"conclusions": [point()], "causes": [], "risks": [], "recommendations": [], **updates}


def test_compact_result_only_resolves_real_references(facts):
    client = Mock()
    client.complete_json.return_value = output(
        risks=[point(title="待确认范围", content="须复核未判定记录。", evidence_refs=["stat1_3"])],
    )
    result = compact.generate(facts, lambda: client)
    assert result["schema_version"] == compact.VERSION
    assert result["degraded"] is False
    assert result["facts"] == {"gate": facts["gate"], "stat1_3": facts["stat1_3"]}
    assert result["facts"]["stat1_3"]["value"] == 263479
    assert "raw_content" not in client.complete_json.call_args.kwargs["user"]
    client.close.assert_called_once()


@pytest.mark.parametrize("invalid", [
    output(conclusions=[point()] * 3),
    output(causes=[point()] * 4),
    output(risks=[point(content="待" * 51)]),
    output(risks=[point()] * 3),
    output(recommendations=[point(priority="HIGH")] * 4),
    output(conclusions=[point(title="长" * 17)]),
    output(conclusions=[point(content="长" * 61)]),
    output(conclusions=[point(content="查阅 UNKNOWN 字段")]),
    output(conclusions=[point(evidence_refs=["gate", "violations", "stat1_3", "stat1_4"])]),
    output(conclusions=[point(evidence_refs=["invented"])]),
    output(causes=[point()]),
    output(recommendations=[point(priority="URGENT")]),
    output(extra=[]),
])
def test_invalid_output_retries_once_then_uses_same_fallback(facts, invalid):
    client = Mock()
    client.complete_json.return_value = invalid
    result = compact.generate(facts, lambda: client)
    assert client.complete_json.call_count == 2
    assert result["degraded"] is True
    assert result["sections"] == compact._fallback(facts).model_dump()
    assert result["facts"]["reason1_1"]["value"] == 130093


def test_retry_can_recover(facts):
    client = Mock()
    client.complete_json.side_effect = [output(conclusions=[point(evidence_refs=["invalid"])]), output()]
    assert compact.generate(facts, lambda: client)["degraded"] is False
    assert client.complete_json.call_count == 2


def test_report_cache_is_versioned_and_regeneration_replaces_only_current_version(facts):
    report_id = uuid4()
    jobs = ReportInterpretationJobs()
    client = Mock()
    client.complete_json.side_effect = [
        {"conclusion": {"text": "历史解读", "evidence_keys": ["gate"]}, "causes": [], "risks": [], "recommendations": []},
        output(),
        output(conclusions=[point(title="重新生成完成")]),
    ]

    def start(version, regenerate=False):
        jobs.start(report_id, facts, lambda: client, regenerate=regenerate, version=version)
        return jobs._jobs[(report_id, version)][0].result(timeout=3)

    try:
        legacy = start("r6")
        assert start(compact.VERSION) == start(compact.VERSION)
        assert client.complete_json.call_count == 2
        assert start(compact.VERSION, True)["sections"]["conclusions"][0]["title"] == "重新生成完成"
        assert jobs.status(report_id)["interpretation"] == legacy
    finally:
        jobs._executor.shutdown()

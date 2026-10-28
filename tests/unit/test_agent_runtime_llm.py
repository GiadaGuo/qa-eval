from unittest.mock import Mock
from uuid import uuid4

from qa_eval.application.agent.runtime import AgentRuntime
from qa_eval.infrastructure.llm.client import LLMTransportError


def _runtime_with(client):
    runtime = object.__new__(AgentRuntime)
    runtime.llm_client = client
    return runtime


def _recommendation():
    return {
        "proposal": {
            "items": [
                {
                    "metric": {"key": "0204", "measurement": "ratio"},
                    "selected_calculator": {"key": "structural_completeness"},
                    "rule": {"operator": "GTE", "threshold": 0.95},
                    "limitations": ["Demo threshold"],
                }
            ]
        },
        "validation": {"valid": True},
        "guide_coverage": [
            {"metric_key": "0204", "status": "APPLICABLE", "reason": "selected"}
        ],
    }


def test_agent_uses_model_to_explain_deterministic_tool_result():
    client = Mock(model="test-model")
    client.complete_json.return_value = {"reply": "已根据真实 Proposal 推荐结构完整性检查。"}

    reply, metadata = _runtime_with(client)._model_reply(
        content="推荐评测方案",
        dataset_version_id=uuid4(),
        metric_count=15,
        available_count=2,
        recommendation=_recommendation(),
        fallback="fallback",
    )

    assert reply.startswith("已根据真实 Proposal")
    assert metadata == {"mode": "MODEL", "model": "test-model"}
    prompt = client.complete_json.call_args.kwargs["user"]
    assert "0204" in prompt
    assert "APPLICABLE" in prompt


def test_agent_falls_back_when_model_gateway_fails():
    client = Mock(model="test-model")
    client.complete_json.side_effect = LLMTransportError("offline")

    reply, metadata = _runtime_with(client)._model_reply(
        content="推荐评测方案",
        dataset_version_id=uuid4(),
        metric_count=15,
        available_count=2,
        recommendation=_recommendation(),
        fallback="deterministic reply",
    )

    assert reply == "deterministic reply"
    assert metadata["mode"] == "DETERMINISTIC_FALLBACK"
    assert metadata["reason"] == "LLMTransportError"

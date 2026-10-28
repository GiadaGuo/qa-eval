from unittest.mock import Mock

import pytest

from qa_eval.application.agent.skill_registry import SkillRegistry
from qa_eval.application.agent.tools.adapter import (
    AgentToolAdapter,
    AgentToolCallLimitError,
    AgentToolTimeoutError,
)
from qa_eval.application.agent.tools.registry import ToolNotAllowedError


def test_skill_registry_progressively_loads_data_quality_details():
    registry = SkillRegistry()

    discovered = registry.discover()
    loaded = registry.select("请分析数据质量指标")

    assert discovered[0].key == "data-quality"
    assert loaded.allowed_tools == (
        "get_dataset_profile",
        "discover_metrics",
        "recommend_evaluation_proposal",
    )
    assert loaded.workflow


def test_tool_adapter_enforces_skill_allowlist_and_call_budget():
    adapter = AgentToolAdapter(Mock(), max_calls=0)
    skill = SkillRegistry().load("data-quality")

    with pytest.raises(ToolNotAllowedError):
        adapter.registry.get("create_quality_contract", allowed_tools=skill.allowed_tools)
    with pytest.raises(AgentToolCallLimitError):
        adapter.invoke(skill, "get_dataset_profile", {"dataset_version_id": "bad"})


def test_tool_argument_digest_is_stable_for_key_order():
    left = AgentToolAdapter.arguments_digest({"b": 2, "a": 1})
    right = AgentToolAdapter.arguments_digest({"a": 1, "b": 2})

    assert left == right


def test_tool_adapter_marks_elapsed_deadline_as_timeout(monkeypatch):
    profile = Mock(
        checksum_sha256="a" * 64,
        profiler_key="base",
        profiler_version="1.0.0",
        document={"record_count": 10},
    )
    session = Mock()
    session.scalar.return_value = profile
    adapter = AgentToolAdapter(session, timeout_seconds=1)
    skill = SkillRegistry().load("data-quality")
    ticks = iter((10.0, 11.5))
    monkeypatch.setattr(
        "qa_eval.application.agent.tools.adapter.monotonic", lambda: next(ticks)
    )

    with pytest.raises(AgentToolTimeoutError):
        adapter.invoke(
            skill,
            "get_dataset_profile",
            {"dataset_version_id": "00000000-0000-0000-0000-000000000001"},
        )

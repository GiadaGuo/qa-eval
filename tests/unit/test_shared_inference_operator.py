import json
from pathlib import Path

import pytest

from qa_eval.inference.llm_semantic_judge import (
    InferenceOperatorError,
    LLMSemanticJudgeOperator,
)
from qa_eval.infrastructure.llm.client import LLMTransportError


def _input(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "sample_id": "a",
                "contents": [
                    {
                        "role": "article.body.plain",
                        "payload": {"text": "some text"},
                    }
                ],
            }
        )
        + "\n"
    )
    return path


def _requirements():
    return [
        {
            "prompt_family": "text-quality-semantic-v1",
            "rubric": {"criterion": "coherence"},
            "output_field": "text_coherence",
        }
    ]


class CaptureClient:
    model = "capture-model"

    def __init__(self, output):
        self.output = output
        self.stats = {"calls": 0, "failures": 0}
        self.usage = {}
        self.requests = []

    def complete_json(self, *, system: str, user: str):
        self.stats["calls"] += 1
        self.requests.append((system, user))
        return self.output


def test_unconfigured_provider_has_structured_diagnostic(tmp_path: Path):
    with pytest.raises(InferenceOperatorError) as error:
        LLMSemanticJudgeOperator(None).execute(
            input_path=_input(tmp_path / "input.jsonl"),
            shared_computation_id="shared-1",
            requirements=_requirements(),
            parameters={},
        )
    assert error.value.code == "PROVIDER_NOT_CONFIGURED"


def test_provider_timeout_is_not_converted_to_a_score(tmp_path: Path):
    class TimeoutClient:
        model = "timeout-model"

        def __init__(self):
            self.stats = {"calls": 0, "failures": 0}
            self.usage = {}

        def complete_json(self, *, system: str, user: str):
            del system, user
            self.stats["calls"] += 1
            raise LLMTransportError("timed out")

    with pytest.raises(LLMTransportError, match="timed out"):
        LLMSemanticJudgeOperator(TimeoutClient()).execute(
            input_path=_input(tmp_path / "input.jsonl"),
            shared_computation_id="shared-1",
            requirements=_requirements(),
            parameters={},
        )


def test_multiple_content_roles_and_metric_static_context_are_separate(tmp_path: Path):
    path = tmp_path / "input.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "a",
                "contents": [
                    {"role": "article.body.plain", "payload": {"text": "正文"}},
                    {"role": "article.title", "payload": {"text": "标题"}},
                    {"role": "article.summary", "payload": {"text": "摘要"}},
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    schema = {
        "type": "object",
        "required": ["status", "value", "evidence"],
        "properties": {
            "status": {"const": "OK"},
            "value": {"type": "boolean"},
            "evidence": {"type": "array"},
        },
    }
    requirements = [
        {
            "prompt_family": "family",
            "prompt_version": "1.0",
            "rubric_version": "a",
            "rubric": {"criterion": "title-body"},
            "static_context": {"declared_dataset_type": "行业通识"},
            "output_schema": schema,
            "output_field": "consistent",
            "input_roles": [
                {"alias": "body", "role": "article.body.plain", "required": True},
                {"alias": "title", "role": "article.title", "required": True},
            ],
        },
        {
            "prompt_family": "family",
            "prompt_version": "1.0",
            "rubric_version": "b",
            "rubric": {"criterion": "summary"},
            "static_context": {"scope": "policy"},
            "output_schema": schema,
            "output_field": "summary_ok",
            "input_roles": [
                {"alias": "summary", "role": "article.summary", "required": True}
            ],
        },
    ]
    client = CaptureClient(
        {
            "outputs": {
                "consistent": {"status": "OK", "value": True, "evidence": []},
                "summary_ok": {"status": "OK", "value": True, "evidence": []},
            }
        }
    )

    output = LLMSemanticJudgeOperator(client).execute(
        input_path=path,
        shared_computation_id="shared",
        requirements=requirements,
        parameters={"sample_limit": 1},
    )

    assert output.records[0]["outputs"]["consistent"]["value"] is True
    prompt = client.requests[0][1]
    assert '"body": "正文"' in prompt
    assert '"title": "标题"' in prompt
    assert '"summary": "摘要"' in prompt
    assert "declared_dataset_type" in prompt
    assert '"scope": "policy"' in prompt


def test_invalid_rubric_value_is_rejected_without_coercion(tmp_path: Path):
    requirements = [
        {
            **_requirements()[0],
            "output_schema": {
                "type": "object",
                "required": ["status", "value", "evidence"],
                "properties": {
                    "status": {"const": "OK"},
                    "value": {"type": "boolean"},
                    "evidence": {"type": "array"},
                },
            },
        }
    ]
    client = CaptureClient(
        {"outputs": {"text_coherence": {"status": "OK", "value": "yes", "evidence": []}}}
    )
    output = LLMSemanticJudgeOperator(client).execute(
        input_path=_input(tmp_path / "input.jsonl"),
        shared_computation_id="shared",
        requirements=requirements,
        parameters={},
    )
    assert output.records[0]["outputs"] == {}
    assert output.records[0]["output_status"]["text_coherence"]["status"] == "ERROR"

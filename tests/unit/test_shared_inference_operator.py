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

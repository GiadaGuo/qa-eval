from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from qa_eval.contracts.base import JsonObject
from qa_eval.infrastructure.llm.client import StructuredLLMClient


class InferenceOperatorError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class SharedInferenceOutput:
    records: list[JsonObject]
    stats: JsonObject
    audit: JsonObject


class LLMSemanticJudgeOperator:
    operator_type = "llm_semantic_judge"

    def __init__(self, client: StructuredLLMClient | None):
        self.client = client

    def execute(
        self,
        *,
        input_path: Path,
        shared_computation_id: str,
        requirements: list[JsonObject],
        parameters: JsonObject,
    ) -> SharedInferenceOutput:
        if self.client is None:
            raise InferenceOperatorError(
                "PROVIDER_NOT_CONFIGURED",
                "Shared inference requires QA_EVAL_LLM_BASE_URL, "
                "QA_EVAL_LLM_API_KEY, and QA_EVAL_LLM_MODEL",
            )
        if not requirements:
            raise InferenceOperatorError("NO_REQUIREMENTS", "No inference requirements")
        sample_limit = int(parameters.get("sample_limit", 20))
        max_chars = int(parameters.get("max_input_chars", 12000))
        content_role = str(parameters.get("content_role", "article.body.plain"))
        samples = self._read_samples(input_path, content_role, sample_limit)
        if not samples:
            raise InferenceOperatorError("NO_INPUT", "No matching text content was found")

        system, rubric_prompt = self._prompt(requirements)
        records: list[JsonObject] = []
        started = time.perf_counter()
        truncated_count = 0
        calls_before = self._client_stat("calls")
        failures_before = self._client_stat("failures")
        usage_before = self._client_usage()
        for sample_id, text in samples:
            truncated = len(text) > max_chars
            if truncated:
                truncated_count += 1
            presented = text[:max_chars]
            inference_id = str(uuid4())
            response = self.client.complete_json(
                system=system,
                user=(
                    f"共享推理 ID: {shared_computation_id}\n"
                    f"记录 ID: {sample_id}\n"
                    f"截断状态: {'TRUNCATED' if truncated else 'FULL'}\n"
                    f"Rubrics:\n{rubric_prompt}\n\n待评文本:\n{presented}"
                ),
            )
            outputs = response.get("outputs")
            if not isinstance(outputs, dict):
                raise InferenceOperatorError(
                    "INVALID_STRUCTURED_OUTPUT",
                    "LLM response must contain an outputs object",
                    sample_id=sample_id,
                )
            validated: dict[str, JsonObject] = {}
            missing: list[str] = []
            for requirement in requirements:
                field = str(requirement["output_field"])
                value = outputs.get(field)
                if not isinstance(value, dict):
                    missing.append(field)
                    continue
                score = value.get("score")
                if not isinstance(score, (int, float)) or isinstance(score, bool):
                    missing.append(field)
                    continue
                validated[field] = value
            records.append(
                {
                    "sample_id": sample_id,
                    "inference_id": inference_id,
                    "shared_computation_id": shared_computation_id,
                    "input_truncated": truncated,
                    "outputs": validated,
                    "missing_outputs": missing,
                }
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        usage_after = self._client_usage()
        return SharedInferenceOutput(
            records=records,
            stats={
                "provider_calls": self._client_stat("calls") - calls_before,
                "provider_failures": self._client_stat("failures") - failures_before,
                "input_records": len(samples),
                "truncated_records": truncated_count,
                "latency_ms": elapsed_ms,
                "token_usage": self._usage_delta(usage_before, usage_after),
            },
            audit={
                "shared_computation_id": shared_computation_id,
                "operator_type": self.operator_type,
                "model": self.client.model,
                "prompt_family": requirements[0].get("prompt_family"),
                "output_fields": [item["output_field"] for item in requirements],
                "content_role": content_role,
                "sample_limit": sample_limit,
                "max_input_chars": max_chars,
                "truncation_policy": "HEAD_CHARS_WITH_EXPLICIT_RECORD_FLAG",
            },
        )

    @staticmethod
    def _prompt(requirements: list[JsonObject]) -> tuple[str, str]:
        fields = []
        rubric_lines = []
        for item in requirements:
            field = str(item["output_field"])
            fields.append(f'"{field}": {{"score": 0.0, "rationale": "...", "flags": []}}')
            rubric_lines.append(
                f"- 输出字段 {field}: {json.dumps(item['rubric'], ensure_ascii=False, sort_keys=True)}"
            )
        system = (
            "你是数据集文本质量结构化评测器。逐项独立遵循 Rubric，分数必须在 0 到 1。"
            "只返回 JSON 对象，不得把不同指标合并成总分。输出格式为 "
            + '{"outputs": {' + ", ".join(fields) + "}}"
        )
        return system, "\n".join(rubric_lines)

    @staticmethod
    def _read_samples(path: Path, role: str, limit: int) -> list[tuple[str, str]]:
        samples: list[tuple[str, str]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                for content in record.get("contents", []):
                    payload = content.get("payload", {})
                    if content.get("role") == role and isinstance(payload.get("text"), str):
                        samples.append((str(record.get("sample_id")), payload["text"]))
                        break
                if len(samples) >= limit:
                    break
        return samples

    def _client_stat(self, key: str) -> int:
        stats = getattr(self.client, "stats", {})
        return int(stats.get(key, 0)) if isinstance(stats, dict) else 0

    def _client_usage(self) -> dict[str, int]:
        usage = getattr(self.client, "usage", {})
        return dict(usage) if isinstance(usage, dict) else {}

    @staticmethod
    def _usage_delta(before: dict[str, int], after: dict[str, int]) -> JsonObject:
        return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in set(before) | set(after)}

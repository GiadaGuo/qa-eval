from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

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


@dataclass(frozen=True)
class _InputSample:
    sample_id: str
    fields: dict[str, str]


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
        role_map = self._role_map(requirements, parameters)
        samples, source_count = self._read_samples(input_path, role_map, sample_limit)
        if source_count == 0:
            raise InferenceOperatorError("NO_INPUT", "The normalized dataset is empty")

        records: list[JsonObject] = []
        started = time.perf_counter()
        truncated_count = 0
        calls_before = self._client_stat("calls")
        failures_before = self._client_stat("failures")
        usage_before = self._client_usage()
        for sample in samples:
            active, preflight = self._requirements_for_sample(requirements, sample)
            record: JsonObject = {
                "sample_id": sample.sample_id,
                "inference_id": str(uuid4()),
                "shared_computation_id": shared_computation_id,
                "source_record_count": source_count,
                "selected_record_count": len(samples),
                "input_truncated": False,
                "outputs": {},
                "output_status": preflight,
                "missing_outputs": [],
            }
            if not active:
                records.append(record)
                continue
            presented, truncated = self._present_fields(sample.fields, active, max_chars)
            record["input_truncated"] = truncated
            if truncated:
                truncated_count += 1
            system, rubric_prompt = self._prompt(active)
            response = self.client.complete_json(
                system=system,
                user=(
                    f"共享推理 ID: {shared_computation_id}\n"
                    f"记录 ID: {sample.sample_id}\n"
                    f"截断状态: {'TRUNCATED' if truncated else 'FULL'}\n"
                    f"指标要求（各指标上下文相互独立）:\n{rubric_prompt}\n\n"
                    f"关联字段:\n{json.dumps(presented, ensure_ascii=False, sort_keys=True)}"
                ),
            )
            outputs = response.get("outputs")
            if not isinstance(outputs, dict):
                raise InferenceOperatorError(
                    "INVALID_STRUCTURED_OUTPUT",
                    "LLM response must contain an outputs object",
                    sample_id=sample.sample_id,
                )
            for requirement in active:
                field = str(requirement["output_field"])
                normalized = self._normalize_legacy_output(outputs.get(field))
                error = self._validate_output(normalized, requirement)
                if error is not None:
                    record["missing_outputs"].append(field)
                    record["output_status"][field] = {
                        "status": "ERROR",
                        "reason_code": "RUBRIC_OUTPUT_SCHEMA_INVALID",
                        "message": error,
                    }
                    continue
                record["outputs"][field] = normalized
                record["output_status"][field] = {
                    "status": normalized.get("status", "OK")
                }
            records.append(record)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        usage_after = self._client_usage()
        return SharedInferenceOutput(
            records=records,
            stats={
                "provider_calls": self._client_stat("calls") - calls_before,
                "provider_failures": self._client_stat("failures") - failures_before,
                "source_records": source_count,
                "selected_records": len(samples),
                "truncated_records": truncated_count,
                "latency_ms": elapsed_ms,
                "token_usage": self._usage_delta(usage_before, usage_after),
            },
            audit={
                "shared_computation_id": shared_computation_id,
                "operator_type": self.operator_type,
                "model": self.client.model,
                "prompt_family": requirements[0].get("prompt_family"),
                "prompt_version": requirements[0].get("prompt_version"),
                "rubric_versions": {
                    item["output_field"]: item.get("rubric_version")
                    for item in requirements
                },
                "output_fields": [item["output_field"] for item in requirements],
                "input_roles": role_map,
                "sample_limit": sample_limit,
                "max_input_chars": max_chars,
                "truncation_policy": "PER_FIELD_HEAD_CHARS_WITH_EXPLICIT_RECORD_FLAG",
            },
        )

    @staticmethod
    def _role_map(
        requirements: list[JsonObject], parameters: JsonObject
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for requirement in requirements:
            for item in requirement.get("input_roles", []):
                result[str(item["alias"])] = str(item["role"])
        overrides = parameters.get("content_roles", {})
        if isinstance(overrides, dict):
            result.update({str(key): str(value) for key, value in overrides.items()})
        if not result:
            result["body"] = str(
                parameters.get("content_role", "article.body.plain")
            )
        return result

    @staticmethod
    def _requirements_for_sample(
        requirements: list[JsonObject], sample: _InputSample
    ) -> tuple[list[JsonObject], dict[str, JsonObject]]:
        active = []
        status: dict[str, JsonObject] = {}
        for requirement in requirements:
            field = str(requirement["output_field"])
            preflight = requirement.get("preflight_status")
            if isinstance(preflight, dict):
                status[field] = preflight
                continue
            missing = [
                str(item["alias"])
                for item in requirement.get("input_roles", [])
                if item.get("required", True) and item["alias"] not in sample.fields
            ]
            context = requirement.get("static_context", {})
            missing_context = [
                key
                for key in requirement.get("required_static_context", [])
                if key not in context
            ]
            if missing:
                status[field] = {
                    "status": "MISSING",
                    "reason_code": "REQUIRED_CONTENT_ROLE_MISSING",
                    "missing_roles": missing,
                }
            elif missing_context:
                status[field] = {
                    "status": "MISSING",
                    "reason_code": "REQUIRED_STATIC_CONTEXT_MISSING",
                    "missing_context": missing_context,
                }
            else:
                active.append(requirement)
        return active, status

    @staticmethod
    def _present_fields(
        fields: dict[str, str], requirements: list[JsonObject], max_chars: int
    ) -> tuple[dict[str, str], bool]:
        aliases = {
            str(item["alias"])
            for requirement in requirements
            for item in requirement.get("input_roles", [])
        } or set(fields)
        presented = {}
        truncated = False
        for alias in sorted(aliases):
            value = fields.get(alias)
            if value is None:
                continue
            if len(value) > max_chars:
                truncated = True
            presented[alias] = value[:max_chars]
        return presented, truncated

    @staticmethod
    def _prompt(requirements: list[JsonObject]) -> tuple[str, str]:
        fields = []
        blocks = []
        for item in requirements:
            field = str(item["output_field"])
            fields.append(f'"{field}": {{...}}')
            blocks.append(
                json.dumps(
                    {
                        "output_field": field,
                        "rubric_version": item.get("rubric_version"),
                        "rubric": item["rubric"],
                        "static_context": item.get("static_context", {}),
                        "output_schema": LLMSemanticJudgeOperator._schema_for(item),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        system = (
            "你是数据集文本质量结构化评测器。每个指标必须独立遵循自己的 Rubric、"
            "静态上下文和 JSON Schema。不得合并指标语义，不得臆造缺失字段。只返回 "
            + '{"outputs": {'
            + ", ".join(fields)
            + "}}"
        )
        return system, "\n".join(f"- {block}" for block in blocks)

    @staticmethod
    def _normalize_legacy_output(value: Any) -> Any:
        if isinstance(value, dict) and "score" in value and "value" not in value:
            return {
                "status": "OK",
                "value": value["score"],
                "evidence": value.get("flags", []),
                "rationale": value.get("rationale"),
                **(
                    {"confidence": value["confidence"]}
                    if "confidence" in value
                    else {}
                ),
            }
        return value

    @staticmethod
    def _validate_output(value: Any, requirement: JsonObject) -> str | None:
        errors = sorted(
            Draft202012Validator(
                LLMSemanticJudgeOperator._schema_for(requirement)
            ).iter_errors(value),
            key=lambda error: list(error.path),
        )
        if not errors:
            return None
        first = errors[0]
        location = ".".join(str(item) for item in first.path) or "$"
        return f"{location}: {first.message}"

    @staticmethod
    def _schema_for(requirement: JsonObject) -> JsonObject:
        schema = requirement.get("output_schema")
        if isinstance(schema, dict):
            return schema
        return {
            "type": "object",
            "required": ["status", "value", "evidence"],
            "properties": {
                "status": {"const": "OK"},
                "value": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"type": "array"},
                "rationale": {"type": ["string", "null"]},
            },
        }

    @staticmethod
    def _read_samples(
        path: Path, role_map: dict[str, str], limit: int
    ) -> tuple[list[_InputSample], int]:
        samples: list[_InputSample] = []
        source_count = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                source_count += 1
                if len(samples) >= limit:
                    continue
                record = json.loads(line)
                by_role = {
                    str(content.get("role")): content.get("payload", {}).get("text")
                    for content in record.get("contents", [])
                    if content.get("presence", "PRESENT") == "PRESENT"
                    and isinstance(content.get("payload", {}).get("text"), str)
                }
                fields = {
                    alias: by_role[role]
                    for alias, role in role_map.items()
                    if role in by_role
                }
                samples.append(
                    _InputSample(sample_id=str(record.get("sample_id")), fields=fields)
                )
        return samples, source_count

    def _client_stat(self, key: str) -> int:
        stats = getattr(self.client, "stats", {})
        return int(stats.get(key, 0)) if isinstance(stats, dict) else 0

    def _client_usage(self) -> dict[str, int]:
        usage = getattr(self.client, "usage", {})
        return dict(usage) if isinstance(usage, dict) else {}

    @staticmethod
    def _usage_delta(before: dict[str, int], after: dict[str, int]) -> JsonObject:
        return {
            key: int(after.get(key, 0)) - int(before.get(key, 0))
            for key in set(before) | set(after)
        }

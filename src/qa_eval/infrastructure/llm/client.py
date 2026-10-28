# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# OpenAI 兼容 Chat Completions 客户端：
# - 结构化 JSON 输出（response_format + 容错抽取代码块）
# - 超时与传输级重试（应用级"校验失败重试"由评测器负责）
# - 调用统计（calls / failures），供任务审计落库
# ====================================================

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import httpx

from qa_eval.config import Settings

logger = logging.getLogger(__name__)

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class LLMError(Exception):
    """LLM 调用失败（传输或响应格式）。"""


class LLMTransportError(LLMError):
    """网络错误、超时或非 2xx HTTP 状态。"""


class LLMResponseError(LLMError):
    """响应缺少 choices/message 或内容不是 JSON 对象。"""


class StructuredLLMClient(Protocol):
    """应用层依赖的最小接口：一次调用返回一个 JSON 对象。"""

    model: str

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]: ...


class OpenAICompatibleLLMClient:
    """POST {base_url}/chat/completions 的轻量封装。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        timeout_seconds: float = 120.0,
        transport_retries: int = 2,
        max_output_tokens: int = 4096,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.transport_retries = transport_retries
        self.max_output_tokens = max_output_tokens
        self._http = http_client or httpx.Client(timeout=timeout_seconds)
        # 调用统计：供任务执行器写入审计信息
        self.stats = {"calls": 0, "failures": 0}

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatibleLLMClient | None:
        """三项必要配置任一缺失即视为未配置，返回 None。"""

        if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
            return None
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            transport_retries=settings.llm_transport_retries,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        """发起一次结构化调用；传输错误重试 transport_retries 次。"""

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        last_exc: Exception | None = None
        for _attempt in range(self.transport_retries + 1):
            self.stats["calls"] += 1
            try:
                response = self._http.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if response.status_code >= 400:
                    last_exc = LLMTransportError(
                        f"LLM gateway returned HTTP {response.status_code}: "
                        f"{response.text[:300]}"
                    )
                else:
                    try:
                        return self._extract_json_object(response.json())
                    except LLMResponseError:
                        # 响应格式错误不属于传输故障：计入失败后上抛，
                        # 由评测层的校验反馈重试兜底
                        self.stats["failures"] += 1
                        raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = LLMTransportError(f"LLM transport failure: {exc}")
            except json.JSONDecodeError as exc:
                last_exc = LLMResponseError(
                    f"LLM gateway returned non-JSON body: {exc}"
                )
            logger.warning("LLM call failed, retrying: %s", last_exc)

        self.stats["failures"] += 1
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _extract_json_object(body: dict[str, Any]) -> dict[str, Any]:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"LLM response missing choices/message: {body!r:.300}"
            ) from exc
        if not isinstance(content, str):
            raise LLMResponseError("LLM message content is not a string")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            fenced = _JSON_FENCE_PATTERN.search(content)
            if fenced is None:
                raise LLMResponseError("LLM content is not a JSON object") from None
            try:
                parsed = json.loads(fenced.group(1))
            except json.JSONDecodeError as exc:
                raise LLMResponseError("LLM fenced content is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("LLM content is not a JSON object")
        return parsed

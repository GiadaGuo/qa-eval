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
import random
import re
import time
from typing import Any, Protocol

import httpx

from qa_eval.config import Settings

logger = logging.getLogger(__name__)

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class LLMError(Exception):
    """LLM 调用失败（传输或响应格式）。"""


class LLMTransportError(LLMError):
    """网络错误、超时或非 2xx HTTP 状态。"""

    def __init__(
        self,
        message: str,
        *,
        error_category: str = "TRANSPORT_ERROR",
        retryable: bool = True,
        errno: int | None = None,
        http_status: int | None = None,
        retry_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_category = error_category
        self.retryable = retryable
        self.errno = errno
        self.http_status = http_status
        self.retry_exhausted = retry_exhausted

    def metadata(self) -> dict[str, Any]:
        return {
            "error_category": self.error_category,
            "retryable": self.retryable,
            "errno": self.errno,
            "http_status": self.http_status,
            "retry_exhausted": self.retry_exhausted,
        }


class LLMRequestRejectedError(LLMTransportError):
    """Provider 明确拒绝请求；相同请求不可重试。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        http_status: int,
        error_code: str | int | None,
        error_category: str,
        request_id: str | None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.http_status = http_status
        self.error_code = error_code
        self.error_category = error_category
        self.retryable = False
        self.request_id = request_id

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "http_status": self.http_status,
            "error_code": self.error_code,
            "error_category": self.error_category,
            "retryable": self.retryable,
            "request_id": self.request_id,
        }


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
        retry_base_delay_seconds: float = 0.5,
        retry_max_delay_seconds: float = 5.0,
        retry_total_budget_seconds: float = 250.0,
        max_output_tokens: int = 4096,
        thinking: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.transport_retries = transport_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.retry_total_budget_seconds = retry_total_budget_seconds
        self.max_output_tokens = max_output_tokens
        self.thinking = thinking
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(timeout_seconds, 10.0),
                pool=min(timeout_seconds, 5.0),
                write=min(timeout_seconds, 30.0),
            )
        )
        # 调用统计：供任务执行器写入审计信息
        self.stats = {"calls": 0, "failures": 0}
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

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
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
            retry_total_budget_seconds=settings.llm_retry_total_budget_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
            thinking=settings.llm_thinking,
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
        if self.thinking:
            # 推理模型（如 kimi-k2.6）关闭思考：避免 reasoning_content
            # 耗尽 max_tokens 导致 content 为空。参数形态依网关而定。
            payload["thinking"] = {"type": self.thinking}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        last_exc: Exception | None = None
        started = time.monotonic()
        for attempt in range(self.transport_retries + 1):
            remaining_budget = self.retry_total_budget_seconds - (
                time.monotonic() - started
            )
            if remaining_budget <= 0:
                break
            self.stats["calls"] += 1
            try:
                response = self._http.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=min(self.timeout_seconds, remaining_budget),
                )
                if response.status_code >= 400:
                    rejection = self._request_rejection(response)
                    if rejection is not None:
                        self.stats["failures"] += 1
                        logger.warning(
                            "LLM request rejected: category=%s status=%s provider=%s "
                            "model=%s request_id=%s",
                            rejection.error_category,
                            rejection.http_status,
                            rejection.provider,
                            rejection.model,
                            rejection.request_id,
                        )
                        raise rejection
                    category = (
                        "RATE_LIMITED"
                        if response.status_code == 429
                        else "PROVIDER_TIMEOUT"
                        if response.status_code == 408
                        else "PROVIDER_SERVER_ERROR"
                    )
                    last_exc = LLMTransportError(
                        f"LLM gateway returned HTTP {response.status_code}",
                        error_category=category,
                        http_status=response.status_code,
                    )
                else:
                    try:
                        body = response.json()
                        usage = body.get("usage", {})
                        if isinstance(usage, dict):
                            for key in self.usage:
                                value = usage.get(key)
                                if isinstance(value, int):
                                    self.usage[key] += value
                        return self._extract_json_object(body)
                    except LLMResponseError:
                        # 响应格式错误不属于传输故障：计入失败后上抛，
                        # 由评测层的校验反馈重试兜底
                        self.stats["failures"] += 1
                        raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                errno = self._find_errno(exc)
                category = (
                    "NETWORK_ADDRESS_UNAVAILABLE"
                    if errno in {49, 99}
                    else "NETWORK_TIMEOUT"
                    if isinstance(exc, httpx.TimeoutException)
                    else "TRANSPORT_ERROR"
                )
                last_exc = LLMTransportError(
                    f"LLM transport failure: {exc}",
                    error_category=category,
                    errno=errno,
                )
            except json.JSONDecodeError as exc:
                last_exc = LLMResponseError(
                    f"LLM gateway returned non-JSON body: {exc}"
                )
            if attempt < self.transport_retries:
                delay = min(
                    self.retry_max_delay_seconds,
                    self.retry_base_delay_seconds * (2**attempt),
                )
                delay += random.uniform(0, delay * 0.25) if delay > 0 else 0
                remaining_budget = self.retry_total_budget_seconds - (
                    time.monotonic() - started
                )
                if delay >= remaining_budget:
                    break
                logger.warning(
                    "LLM call failed, retrying: category=%s attempt=%d/%d "
                    "delay_seconds=%.3f",
                    getattr(last_exc, "error_category", type(last_exc).__name__),
                    attempt + 1,
                    self.transport_retries + 1,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)

        self.stats["failures"] += 1
        if last_exc is None:
            last_exc = LLMTransportError(
                "LLM retry time budget exhausted before an attempt could start",
                error_category="RETRY_BUDGET_EXHAUSTED",
                retryable=False,
                retry_exhausted=True,
            )
        elif isinstance(last_exc, LLMTransportError):
            last_exc.retryable = False
            last_exc.retry_exhausted = True
        raise last_exc

    def close(self) -> None:
        """Release the owned connection pool deterministically."""

        if self._owns_http_client and not self._http.is_closed:
            self._http.close()

    @staticmethod
    def _find_errno(exc: BaseException) -> int | None:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            value = getattr(current, "errno", None)
            if isinstance(value, int):
                return value
            current = current.__cause__ or current.__context__
        return None

    def _request_rejection(
        self, response: httpx.Response
    ) -> LLMRequestRejectedError | None:
        """Classify non-retryable 4xx without retaining response bodies/prompts."""

        if not 400 <= response.status_code < 500 or response.status_code in {408, 429}:
            return None
        try:
            body = response.json()
        except (json.JSONDecodeError, TypeError, ValueError):
            body = {}
        error = body.get("error", {}) if isinstance(body, dict) else {}
        if not isinstance(error, dict):
            error = {}
        raw_type = str(error.get("type") or "")
        raw_code = error.get("code")
        category = (
            "CONTENT_FILTER_REJECTED"
            if raw_type == "content_filter" or raw_code == "content_filter"
            else "REQUEST_REJECTED"
        )
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or (
                str(body.get("request_id"))
                if isinstance(body, dict) and body.get("request_id")
                else None
            )
        )
        provider = self.base_url
        return LLMRequestRejectedError(
            f"LLM request rejected ({category}, HTTP {response.status_code})",
            provider=provider,
            model=self.model,
            http_status=response.status_code,
            error_code=raw_code,
            error_category=category,
            request_id=request_id,
        )

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

        # 1. 直接 JSON
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        # 2. 代码块围栏 ```json ... ``` / ``` ... ```
        fenced = _JSON_FENCE_PATTERN.search(content)
        if fenced is not None:
            try:
                parsed = json.loads(fenced.group(1))
            except json.JSONDecodeError as exc:
                raise LLMResponseError("LLM fenced content is not valid JSON") from exc
            if isinstance(parsed, dict):
                return parsed
            raise LLMResponseError("LLM fenced content is not a JSON object")

        # 3. 散文包裹的裸 JSON：取最外层 { ... }（推理模型常在前加说明文字）
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(content[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMResponseError("LLM content is not a JSON object") from exc
            if isinstance(parsed, dict):
                return parsed

        raise LLMResponseError("LLM content is not a JSON object")

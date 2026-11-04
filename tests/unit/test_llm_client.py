# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# OpenAI 兼容 LLM 客户端单测：MockTransport 模拟成功、
# 代码块抽取、非 JSON、超时、非 2xx 与统计。
# ====================================================

import json

import httpx
import pytest

from qa_eval.config import Settings
from qa_eval.infrastructure.llm.client import (
    LLMRequestRejectedError,
    LLMResponseError,
    LLMTransportError,
    OpenAICompatibleLLMClient,
)


def _client(handler, **overrides) -> OpenAICompatibleLLMClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    params = {
        "base_url": "http://llm.internal/",
        "api_key": "secret",
        "model": "test-model",
        "transport_retries": 1,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
        "retry_total_budget_seconds": 10,
        "http_client": http,
    }
    params.update(overrides)
    return OpenAICompatibleLLMClient(**params)


def _body(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_success_returns_parsed_object_and_records_stats():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_body(json.dumps({"items": []})))

    client = _client(handler)
    result = client.complete_json(system="s", user="u")

    assert result == {"items": []}
    assert client.stats == {"calls": 1, "failures": 0}
    assert len(calls) == 1
    payload = json.loads(calls[0].read())
    assert payload["model"] == "test-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert calls[0].headers["Authorization"] == "Bearer secret"


def test_thinking_disabled_is_sent_in_payload():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_body(json.dumps({"items": []})))

    client = _client(handler, thinking="disabled")
    client.complete_json(system="s", user="u")

    payload = json.loads(calls[0].read())
    assert payload["thinking"] == {"type": "disabled"}


def test_thinking_unset_omits_parameter():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_body(json.dumps({"items": []})))

    client = _client(handler)
    client.complete_json(system="s", user="u")

    payload = json.loads(calls[0].read())
    assert "thinking" not in payload


def test_fenced_json_code_block_is_extracted():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_body('前置说明\n```json\n{"verdict": "ok"}\n```'),
        )

    client = _client(handler)
    assert client.complete_json(system="s", user="u") == {"verdict": "ok"}


def test_non_json_content_raises_after_transport_retries():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("抱歉，我无法输出 JSON。"))

    client = _client(handler)
    with pytest.raises(LLMResponseError):
        client.complete_json(system="s", user="u")
    # 响应格式错误不做传输级重试，由评测层反馈环重试
    assert client.stats["calls"] == 1
    assert client.stats["failures"] == 1


def test_prose_wrapped_bare_json_is_extracted():
    # 推理模型常在 JSON 前附加说明文字，且不带代码块围栏
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_body('根据评测标准，判定结果如下：\n{"verdict": "ok", "score": 3}'),
        )

    client = _client(handler)
    assert client.complete_json(system="s", user="u") == {"verdict": "ok", "score": 3}


def test_prose_with_multiple_json_objects_still_raises():
    # 最外层含多个 JSON 对象（数组元素）时无法收敛为单一对象，仍应报错
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_body('结果如下：\n[{"verdict": "ok"}, {"verdict": "ng"}]'),
        )

    client = _client(handler)
    with pytest.raises(LLMResponseError):
        client.complete_json(system="s", user="u")


def test_timeout_raises_transport_error():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("gateway down")

    client = _client(handler)
    with pytest.raises(LLMTransportError, match="transport failure"):
        client.complete_json(system="s", user="u")


def test_errno_49_is_bounded_and_classified():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        try:
            raise OSError(49, "Can't assign requested address")
        except OSError as cause:
            raise httpx.ConnectError("address unavailable") from cause

    client = _client(handler, transport_retries=2)
    with pytest.raises(LLMTransportError) as captured:
        client.complete_json(system="s", user="u")

    assert calls == 3
    assert captured.value.metadata() == {
        "error_category": "NETWORK_ADDRESS_UNAVAILABLE",
        "retryable": False,
        "errno": 49,
        "http_status": None,
        "retry_exhausted": True,
    }


def test_owned_http_client_can_be_closed():
    client = OpenAICompatibleLLMClient(
        base_url="http://llm.internal",
        api_key="secret",
        model="test-model",
    )
    assert client._http.is_closed is False
    client.close()
    assert client._http.is_closed is True
    client.close()


def test_zero_retry_budget_fails_without_sending_request():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_body('{"ok": true}'))

    client = _client(handler, retry_total_budget_seconds=0)
    with pytest.raises(LLMTransportError) as captured:
        client.complete_json(system="s", user="u")

    assert calls == 0
    assert captured.value.error_category == "RETRY_BUDGET_EXHAUSTED"
    assert captured.value.retryable is False
    assert captured.value.retry_exhausted is True


def test_http_error_status_raises_transport_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client(handler)
    with pytest.raises(LLMTransportError, match="HTTP 500"):
        client.complete_json(system="s", user="u")


def test_content_filter_rejection_is_classified_and_never_retried():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            headers={"x-request-id": "req-filtered"},
            json={
                "error": {
                    "code": 400,
                    "message": "The request included private prompt content",
                    "param": "prompt",
                    "type": "content_filter",
                }
            },
        )

    client = _client(handler, transport_retries=5)
    with pytest.raises(LLMRequestRejectedError) as captured:
        client.complete_json(system="secret-system", user="secret-article")

    assert calls == 1
    assert client.stats == {"calls": 1, "failures": 1}
    assert captured.value.metadata() == {
        "provider": "http://llm.internal",
        "model": "test-model",
        "http_status": 400,
        "error_code": 400,
        "error_category": "CONTENT_FILTER_REJECTED",
        "retryable": False,
        "request_id": "req-filtered",
    }
    assert "private prompt content" not in str(captured.value)
    assert "secret-article" not in str(captured.value)


def test_from_settings_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 清空 pydantic-settings 可能读取到的 LLM 环境变量，避免本地 .env 污染
    for key in ("QA_EVAL_LLM_BASE_URL", "QA_EVAL_LLM_API_KEY", "QA_EVAL_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert OpenAICompatibleLLMClient.from_settings(Settings(_env_file=None)) is None


def test_from_settings_builds_client_when_configured():
    client = OpenAICompatibleLLMClient.from_settings(
        Settings(
            _env_file=None,
            llm_base_url="http://llm.internal",
            llm_api_key="secret",
            llm_model="test-model",
        )
    )
    assert client is not None
    assert client.model == "test-model"
    assert client.base_url == "http://llm.internal"

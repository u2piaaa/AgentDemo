import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import json

import httpx
import pytest

from app.core.config import Settings
from app.services.model_gateway import ModelGateway, ModelResponseError


def settings(**overrides):
    return Settings(_env_file=None, **{
        "llm_provider": "deepseek",
        "llm_api_key": "test-chat-key",
        "deepseek_api_key": "test-legacy-key",
        "openai_api_key": "test-embedding-key",
        "llm_base_url": "https://model.example/v1",
        "openai_base_url": "https://embedding.example/v1",
        "llm_retry_base_seconds": 0,
        "llm_retry_max_seconds": 0,
        "embedding_dimensions": 2,
        **overrides,
    })


def event(content=None, **choice_fields):
    return "data: " + json.dumps({
        "choices": [{"delta": {"content": content}, **choice_fields}],
    }, ensure_ascii=False) + "\n\n"


def success(content="Hello"):
    return httpx.Response(200, text=event(content) + event(finish_reason="stop"))


async def reply(gateway):
    return [part async for part in gateway.stream_reply("test-model", "Hi", [])]


class ScriptedStream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_configurable_provider_payload_and_caller_owned_client():
    requests = []

    def handler(request):
        requests.append(request)
        return success()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(settings(
            llm_provider="compatible", llm_max_output_tokens=123,
            llm_output_token_parameter="max_completion_tokens", llm_reasoning_effort="low",
        ), client)
        assert gateway.route("conversation", "Hi").provider == "compatible"
        assert await reply(gateway) == ["Hello"]
        assert not client.is_closed
    request = requests[0]
    assert str(request.url) == "https://model.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-chat-key"
    payload = json.loads(request.content)
    assert payload["max_completion_tokens"] == 123
    assert payload["reasoning_effort"] == "low"
    assert "temperature" not in payload and "max_tokens" not in payload


@pytest.mark.asyncio
async def test_legacy_key_fallback_and_explicit_keyless_local_provider():
    headers = []

    def handler(request):
        headers.append(request.headers)
        return success()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await reply(ModelGateway(settings(llm_api_key=""), client))
        await reply(ModelGateway(settings(
            llm_api_key="", llm_provider="local", llm_api_key_required=False,
        ), client))
        with pytest.raises(RuntimeError, match="Missing API key"):
            await reply(ModelGateway(settings(llm_api_key="", llm_provider="other"), client))
    assert headers[0]["Authorization"] == "Bearer test-legacy-key"
    assert "Authorization" not in headers[1]
    assert len(headers) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_transient_status_retries_before_emitting(status):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"}) if calls == 1 else success()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await reply(ModelGateway(settings(), client)) == ["Hello"]
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 501])
async def test_permanent_http_errors_do_not_retry(status):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await reply(ModelGateway(settings(), client))
    assert calls == 1


@pytest.mark.asyncio
async def test_transport_retry_budget_is_bounded():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            await reply(ModelGateway(settings(llm_max_retries=2), client))
    assert calls == 3


@pytest.mark.asyncio
async def test_stream_read_failure_before_text_can_retry_and_closes_response():
    failed = ScriptedStream(b": keepalive\n\n", httpx.ReadError("interrupted"))
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=failed) if calls == 1 else success()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await reply(ModelGateway(settings(), client)) == ["Hello"]
    assert calls == 2 and failed.closed


@pytest.mark.asyncio
async def test_partial_output_is_never_replayed():
    calls = 0
    stream = ScriptedStream(event("Partial").encode(), httpx.ReadError("disconnected"))

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=stream)

    parts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            async for part in ModelGateway(settings(), client).stream_reply("test", "Hi", []):
                parts.append(part)
    assert parts == ["Partial"]
    assert calls == 1 and stream.closed


@pytest.mark.asyncio
async def test_sse_comments_multiline_crlf_unicode_reasoning_and_usage():
    raw = (
        ': ping\r\nevent: message\r\ndata: {"choices": [\r\n'
        'data: {"delta": {"reasoning_content": "private", "tool_calls": []}}]}\r\n\r\n'
        'data: {"choices": [], "usage": {"total_tokens": 12}}\r\n\r\n'
        + event("你好") + "data: [DONE]\n\n"
    ).encode()
    stream = ScriptedStream(*(raw[index:index + 1] for index in range(len(raw))))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)
    )) as client:
        assert await reply(ModelGateway(settings(), client)) == ["你好"]
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body,expected", [
    (event("Partial"), "completion marker"),
    ('data: [DONE]', "completion marker"),
    ('data: invalid\n\n', "invalid JSON"),
    ('data: []\n\n', "response object"),
    ('data: {"error": "secret raw <tool_call>"}\n\n', "error response"),
    ('data: {"choices": "broken"}\n\n', "stream choices"),
    ('data: {"choices": [{"delta": "broken"}]}\n\n', "stream delta"),
    (event([{"type": "text", "text": "broken"}]), "non-text"),
    (event(finish_reason="length"), "token limit"),
    (event(finish_reason="content_filter"), "did not complete"),
    (event(finish_reason="tool_calls"), "did not complete"),
])
async def test_invalid_or_incomplete_stream_fails_without_retry(body, expected):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelResponseError, match=expected) as error:
            await reply(ModelGateway(settings(), client))
    assert "secret raw" not in str(error.value)
    assert calls == 1


@pytest.mark.asyncio
async def test_stream_event_size_is_bounded(monkeypatch):
    monkeypatch.setattr("app.services.model_gateway.MAX_SSE_EVENT_CHARS", 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=event("x" * 128))
    )) as client:
        with pytest.raises(ModelResponseError, match="size limit"):
            await reply(ModelGateway(settings(), client))


@pytest.mark.asyncio
async def test_total_deadline_includes_retry_backoff():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError, match="total time budget"):
            await reply(ModelGateway(settings(
                llm_request_timeout_seconds=0.02, llm_retry_max_seconds=1,
            ), client))
    assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    entered = asyncio.Event()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(reply(ModelGateway(settings(), client)))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == 1


@pytest.mark.parametrize("retry_after", [
    "900", "bad", "NaN", "Infinity", "-100",
    format_datetime(datetime.now(UTC) + timedelta(minutes=10)),
])
def test_retry_after_is_bounded_for_numeric_and_http_date_values(retry_after):
    gateway = ModelGateway(settings(llm_retry_max_seconds=3))
    response = httpx.Response(429, headers={"Retry-After": retry_after},
                              request=httpx.Request("POST", "https://model.example"))
    error = httpx.HTTPStatusError("busy", request=response.request, response=response)
    delay = gateway._retry_delay(error, 0)
    assert 0 <= delay <= 3
    if retry_after == "900" or "," in retry_after:
        assert delay == 3


@pytest.mark.asyncio
async def test_embeddings_preserve_order_across_batches_and_retry_only_failed_batch():
    batches = []

    def handler(request):
        batch = json.loads(request.content)["input"]
        batches.append(batch)
        assert request.headers["Authorization"] == "Bearer test-embedding-key"
        if len(batches) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [
            {"index": index, "embedding": [float(text), 1.0]}
            for index, text in reversed(list(enumerate(batch)))
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(settings(embedding_batch_size=2), client)
        assert await gateway.embed_texts(["1", "2", "3"]) == [[1, 1], [2, 1], [3, 1]]
        assert await gateway.embed_texts([]) == []
    assert batches == [["1", "2"], ["3"], ["3"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [], None, ["bad"],
    [{"index": 1, "embedding": [1, 2]}],
    [{"index": True, "embedding": [1, 2]}],
    [{"index": 0, "embedding": [1]}],
    [{"index": 0, "embedding": [True, 2]}],
    [{"index": 0, "embedding": ["1", 2]}],
    [{"index": 0, "embedding": [float("nan"), 2]}],
    [{"index": 0, "embedding": [float("inf"), 2]}],
    [{"index": 0, "embedding": [10 ** 400, 2]}],
    [{"index": 0, "embedding": [0, 0]}],
])
async def test_invalid_embedding_data_is_rejected_before_storage(rows):
    # text intentionally permits NaN/Infinity to exercise invalid provider payloads.
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps({"data": rows}))
    )) as client:
        with pytest.raises(ModelResponseError):
            await ModelGateway(settings(), client).embed_texts(["one"])


def test_duplicate_embedding_indices_are_rejected():
    with pytest.raises(ModelResponseError, match="duplicate indices"):
        ModelGateway(settings())._validate_embeddings({"data": [
            {"index": 0, "embedding": [1, 2]},
            {"index": 0, "embedding": [3, 4]},
        ]}, 2)


@pytest.mark.asyncio
async def test_embedding_deadline_is_total_across_batches():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            await asyncio.sleep(1)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError, match="Embedding request exceeded"):
            await ModelGateway(settings(
                embedding_batch_size=1, llm_request_timeout_seconds=0.05,
            ), client).embed_texts(["first", "second"])
    assert calls == 2

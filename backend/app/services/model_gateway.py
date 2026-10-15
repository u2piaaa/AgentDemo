import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import json
import math
import random
from typing import Any

import httpx

from app.core.config import Settings, get_settings


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_SSE_EVENT_CHARS = 1_000_000


class ModelResponseError(RuntimeError):
    """A provider response cannot safely be treated as a complete model result."""


@dataclass(frozen=True)
class ModelRoute:
    model_name: str
    provider: str
    reason: str


@dataclass(frozen=True)
class StructuredToolPlan:
    no_tool: bool
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    reason: str = ""


class ModelGateway:
    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        # An injected client belongs to the caller; request-local clients are closed here.
        self._http_client = client

    def route(self, task_type: str, prompt: str) -> ModelRoute:
        return ModelRoute(
            model_name=self.settings.llm_chat_model,
            provider=self.settings.llm_provider,
            reason=f"agent_{task_type}_chat",
        )

    def plan_tool_call(
        self,
        prompt: str,
        candidates: list[dict[str, Any]],
    ) -> StructuredToolPlan:
        lowered = prompt.lower()
        for tool in candidates:
            names = [
                str(tool.get("name") or "").lower(),
                str(tool.get("provider_tool_id") or "").lower(),
            ]
            if any(name and name in lowered for name in names):
                return StructuredToolPlan(
                    no_tool=False,
                    tool_name=str(tool["name"]),
                    arguments={},
                    reason="The user explicitly referenced an available tool.",
                )
        return StructuredToolPlan(no_tool=True, reason="No tool is needed for this message.")

    async def normalize_web_search_query(self, message: str) -> str:
        prompt = (
            "Rewrite the user's request as a concise web search engine query.\n"
            "Return only the query text, with no explanation, quotes, markdown, JSON, or label.\n"
            "Keep entities, topic, location, and time intent. Remove answer-format instructions "
            "such as summarize, list bullets, compare, answer in Chinese, or explain.\n"
            "Prefer English query terms when the topic is global news.\n"
            f"User request: {message}"
        )
        route = self.route("web_search_query", prompt)
        parts: list[str] = []
        async for token in self.stream_reply(
            model_name=route.model_name,
            prompt=prompt,
            context=[],
            history=[],
        ):
            parts.append(token)
        return "".join(parts).strip()

    async def stream_reply(
        self,
        model_name: str,
        prompt: str,
        context: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        messages = self._build_messages(prompt, context, history or [])
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            self.settings.llm_output_token_parameter: self.settings.llm_max_output_tokens,
        }
        if self.settings.llm_reasoning_effort is not None:
            payload["reasoning_effort"] = self.settings.llm_reasoning_effort
        else:
            payload["temperature"] = 0.3
        api_key = self.settings.llm_api_key
        if not api_key and self.settings.llm_provider == "deepseek":
            api_key = self.settings.deepseek_api_key
        headers = self._auth_headers(api_key, required=self.settings.llm_api_key_required)
        headers["Accept"] = "text/event-stream"
        url = self._join_url(self.settings.llm_base_url, "/chat/completions")
        emitted = False
        try:
            # HTTPX's read timeout bounds network inactivity, not total generation time.
            # This deadline includes connection setup, all retries, and backoff.
            async with asyncio.timeout(self.settings.llm_request_timeout_seconds):
                async with self._client() as client:
                    for attempt in range(self.settings.llm_max_retries + 1):
                        try:
                            async with client.stream(
                                "POST", url, json=payload, headers=headers, timeout=self._timeout()
                            ) as response:
                                response.raise_for_status()
                                async for data in self._sse_data(response):
                                    if data == "[DONE]":
                                        return
                                    chunk = self._parse_object(data)
                                    token, finished = self._stream_delta(chunk)
                                    if token:
                                        emitted = True
                                        yield token
                                    if finished:
                                        return
                                raise ModelResponseError(
                                    "Model stream ended before a completion marker"
                                )
                        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                            # Replaying after even one emitted token duplicates partial answers.
                            if emitted or not self._should_retry(exc, attempt):
                                raise
                            await asyncio.sleep(self._retry_delay(exc, attempt))
        except TimeoutError as exc:
            raise TimeoutError("Model request exceeded its total time budget") from exc

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = self._auth_headers(self.settings.openai_api_key)
        url = self._join_url(self.settings.openai_base_url, "/embeddings")
        embeddings: list[list[float]] = []
        try:
            async with asyncio.timeout(self.settings.llm_request_timeout_seconds):
                async with self._client() as client:
                    for start in range(0, len(texts), self.settings.embedding_batch_size):
                        batch = texts[start : start + self.settings.embedding_batch_size]
                        data = await self._post_json(
                            client, url,
                            {"model": self.settings.openai_embedding_model, "input": batch},
                            headers,
                        )
                        embeddings.extend(self._validate_embeddings(data, len(batch)))
        except TimeoutError as exc:
            raise TimeoutError("Embedding request exceeded its total time budget") from exc
        return embeddings

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._http_client is not None:
            yield self._http_client
        else:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                yield client

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self.settings.llm_request_timeout_seconds,
            connect=min(
                self.settings.llm_connect_timeout_seconds,
                self.settings.llm_request_timeout_seconds,
            ),
        )

    def _should_retry(self, exc: Exception, attempt: int) -> bool:
        if attempt >= self.settings.llm_max_retries:
            return False
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in RETRYABLE_STATUS_CODES
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))

    def _retry_delay(self, exc: Exception, attempt: int) -> float:
        delay = self.settings.llm_retry_base_seconds * (2 ** attempt) * random.uniform(0.5, 1.0)
        if isinstance(exc, httpx.HTTPStatusError):
            value = exc.response.headers.get("Retry-After", "")
            try:
                retry_after = float(value)
            except ValueError:
                try:
                    retry_after = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    retry_after = delay
            if math.isfinite(retry_after):
                delay = max(delay, retry_after)
        return max(0.0, min(delay, self.settings.llm_retry_max_seconds))

    async def _post_json(
        self, client: httpx.AsyncClient, url: str, payload: dict, headers: dict[str, str]
    ) -> dict[str, Any]:
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=self._timeout()
                )
                response.raise_for_status()
                return self._parse_object(response.text)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if not self._should_retry(exc, attempt):
                    raise
                await asyncio.sleep(self._retry_delay(exc, attempt))
        raise AssertionError("Retry loop exhausted without a result or exception")

    async def _sse_data(self, response: httpx.Response) -> AsyncIterator[str]:
        parts: list[str] = []
        size = 0
        async for line in response.aiter_lines():
            if not line:
                if parts:
                    yield "\n".join(parts)
                    parts = []
                    size = 0
            elif line.startswith("data:") or line == "data":
                value = line.partition(":")[2].removeprefix(" ")
                size += len(value) + 1
                if size > MAX_SSE_EVENT_CHARS:
                    raise ModelResponseError("Model stream event exceeds the size limit")
                parts.append(value)
        # SSE requires a blank line to dispatch. Do not accept a truncated final event.

    def _parse_object(self, data: str) -> dict[str, Any]:
        try:
            parsed = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise ModelResponseError("Model provider returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ModelResponseError("Model provider returned an invalid response object")
        if parsed.get("error") is not None:
            # Never expose the provider's raw body: it can contain credentials or tool markup.
            raise ModelResponseError("Model provider returned an error response")
        return parsed

    def _stream_delta(self, chunk: dict[str, Any]) -> tuple[str, bool]:
        choices = chunk.get("choices")
        if choices == []:  # Usage-only events do not contain text.
            return "", False
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelResponseError("Model provider returned invalid stream choices")
        choice = choices[0]
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            raise ModelResponseError("Model provider returned an invalid stream delta")
        content = delta.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelResponseError("Model provider returned non-text response content")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and finish_reason != "stop":
            if finish_reason == "length":
                raise ModelResponseError("Model output reached its token limit before completion")
            raise ModelResponseError("Model provider did not complete the requested text response")
        # Reasoning and structured tool protocol are not assistant answer text.
        return content or "", finish_reason is not None

    def _validate_embeddings(self, data: dict[str, Any], count: int) -> list[list[float]]:
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != count:
            raise ModelResponseError("Embedding response has an unexpected number of vectors")
        indexed: dict[int, list[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ModelResponseError("Embedding response contains an invalid vector row")
            index, vector = row.get("index"), row.get("embedding")
            if type(index) is not int or not 0 <= index < count or index in indexed:
                raise ModelResponseError("Embedding response contains invalid or duplicate indices")
            if not isinstance(vector, list) or len(vector) != self.settings.embedding_dimensions:
                raise ModelResponseError("Embedding dimensions do not match the configured storage")
            try:
                valid_values = all(
                    type(value) in (int, float) and math.isfinite(value) for value in vector
                )
            except OverflowError:
                valid_values = False
            if not valid_values:
                raise ModelResponseError("Embedding response contains non-finite or non-numeric values")
            if not any(vector):
                raise ModelResponseError("Embedding response contains a zero vector")
            indexed[index] = vector
        return [indexed[index] for index in range(count)]

    async def summarize_messages(
        self,
        messages: list[dict[str, str]],
        existing_summary: str | None = None,
    ) -> str:
        if not messages:
            return existing_summary or ""
        transcript = "\n".join(
            f"{item['role']}: {item['content']}"
            for item in messages
            if item.get("role") in {"user", "assistant"} and item.get("content")
        )
        prompt = (
            "Summarize this conversation for future memory. Keep durable user preferences, "
            "project facts, decisions, and unresolved tasks. Do not include transient filler."
        )
        context = [f"Existing memory summary:\n{existing_summary}"] if existing_summary else []
        context.append(f"Conversation transcript:\n{transcript}")
        route = self.route("memory_summary", prompt)
        parts: list[str] = []
        async for token in self.stream_reply(
            model_name=route.model_name,
            prompt=prompt,
            context=context,
            history=[],
        ):
            parts.append(token)
        return "".join(parts).strip()

    def _build_messages(
        self,
        prompt: str,
        context: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        system = (
            "You are a local personal AI Agent. Answer clearly and practically. "
            "Use the current conversation history as short-term memory for facts the user "
            "asked you to remember, prior preferences, and references such as 'it', 'this "
            "project', or 'that'. Knowledge context is supplemental retrieval material and "
            "may be incomplete. If conversation history and knowledge context conflict about "
            "the user's current project, preferences, or instructions, prefer the conversation "
            "history. If neither conversation history nor knowledge context contains the answer, "
            "say what is missing instead of guessing. When knowledge context includes a source "
            "title or URL, preserve that attribution for factual claims in the answer."
        )
        user_content = prompt
        if context:
            joined_context = "\n\n---\n\n".join(context)
            user_content = (
                "Supplemental knowledge context, which may be incomplete:\n"
                f"{joined_context}\n\n"
                "Current user message:\n"
                f"{prompt}\n\n"
                "Answer the current user message. Use conversation history above for remembered "
                "facts and reference resolution; use the supplemental knowledge context only when "
                "it is relevant."
            )
        safe_history = [
            {"role": item["role"], "content": item["content"]}
            for item in history or []
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        return [
            {"role": "system", "content": system},
            *safe_history,
            {"role": "user", "content": user_content},
        ]

    def _auth_headers(self, api_key: str, *, required: bool = True) -> dict[str, str]:
        if not api_key and required:
            raise RuntimeError("Missing API key for model provider")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _join_url(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _extract_stream_token(self, data: str) -> str:
        return self._stream_delta(self._parse_object(data))[0]

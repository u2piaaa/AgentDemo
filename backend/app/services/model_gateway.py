from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class ModelRoute:
    model_name: str
    provider: str
    reason: str


class ModelGateway:
    def __init__(self) -> None:
        self.settings = get_settings()

    def route(self, task_type: str, prompt: str) -> ModelRoute:
        return ModelRoute(
            model_name=self.settings.llm_chat_model,
            provider="deepseek",
            reason=f"agent_{task_type}_chat",
        )

    async def stream_reply(
        self,
        model_name: str,
        prompt: str,
        context: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        messages = self._build_messages(prompt, context, history or [])
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
        }
        headers = self._auth_headers(self.settings.deepseek_api_key)
        url = self._join_url(self.settings.llm_base_url, "/chat/completions")

        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_seconds) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    token = self._extract_stream_token(data)
                    if token:
                        yield token

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.settings.openai_embedding_model,
            "input": texts,
        }
        headers = self._auth_headers(self.settings.openai_api_key)
        url = self._join_url(self.settings.openai_base_url, "/embeddings")
        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]

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
            "say what is missing instead of guessing."
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

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        if not api_key:
            raise RuntimeError("Missing API key for model provider")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _join_url(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _extract_stream_token(self, data: str) -> str:
        parsed: dict[str, Any] = httpx.Response(200, content=data).json()
        choices = parsed.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""

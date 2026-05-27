from collections.abc import AsyncIterator
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.conversation import Conversation, Message
from app.schemas import ChatRequest
from app.services.model_gateway import ModelGateway
from app.services.plugin_registry import PluginRegistry
from app.services.rag import RagService


class AgentRuntime:
    def __init__(self, session: AsyncSession, plugin_registry: PluginRegistry) -> None:
        self.session = session
        self.plugin_registry = plugin_registry
        self.model_gateway = ModelGateway()
        self.rag = RagService(session)
        self.settings = get_settings()

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, str]]:
        conversation = await self._ensure_conversation(request)
        history = await self._load_recent_history(conversation.id)
        await self._auto_title_conversation(conversation, request.message, history)
        await self._save_message(conversation.id, "user", request.message)

        yield self._event("status", {"label": "retrieving_context"})
        citations = await self.rag.search(request.message, conversation.id)

        yield self._event("status", {"label": "planning"})
        route = self.model_gateway.route(request.task_type, request.message)

        yield self._event("status", {"label": "generating", "model": route.model_name})
        response_parts: list[str] = []
        async for token in self.model_gateway.stream_reply(
            model_name=route.model_name,
            prompt=request.message,
            context=[item.content for item in citations],
            history=history,
        ):
            response_parts.append(token)
            yield self._event("token", {"text": token})

        final_text = "".join(response_parts)
        await self._save_message(
            conversation.id,
            "assistant",
            final_text,
            metadata={"citations": [item.model_dump() for item in citations]},
            model_name=route.model_name,
        )
        yield self._event(
            "done",
            {
                "conversation_id": str(conversation.id),
                "citations": [item.model_dump() for item in citations],
            },
        )

    async def _ensure_conversation(self, request: ChatRequest) -> Conversation:
        if request.conversation_id is not None:
            conversation = await self.session.get(Conversation, request.conversation_id)
            if conversation is not None:
                return conversation

        conversation = Conversation(title="New conversation")
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def _load_recent_history(self, conversation_id: UUID) -> list[dict[str, str]]:
        limit = max(self.settings.agent_memory_message_limit, 0)
        if limit == 0:
            return []

        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return [{"role": message.role, "content": message.content} for message in messages]

    async def _auto_title_conversation(
        self,
        conversation: Conversation,
        message: str,
        history: list[dict[str, str]],
    ) -> None:
        if history or conversation.title.strip() != "New conversation":
            return
        conversation.title = self._summarize_title(message)
        await self.session.commit()

    def _summarize_title(self, message: str) -> str:
        clean = " ".join(message.strip().split())
        if not clean:
            return "New conversation"
        prefixes = (
            "请记住",
            "请",
            "帮我",
            "帮忙",
            "能否",
            "可以",
            "please",
            "can you",
            "could you",
        )
        lowered = clean.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                clean = clean[len(prefix) :].strip(" ：:，,。.!！?")
                break
        return clean[:40].rstrip(" ：:，,。.!！?") or "New conversation"

    async def _save_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
        model_name: str | None = None,
    ) -> None:
        self.session.add(
            Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                model_name=model_name,
                metadata_=metadata or {},
            )
        )
        await self.session.commit()

    def _event(self, event_type: str, data: dict) -> dict[str, str]:
        return {"event": event_type, "data": json.dumps(data)}

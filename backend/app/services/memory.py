"""Incremental, bounded conversation compaction with a durable source cursor.

The cursor lives on the conversation so disabling/deleting a summary does not
recreate the same forgotten facts on the next turn. Recent messages remain raw.
"""

import asyncio
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.response_policy import RAW_TOOL_PROTOCOL_MARKERS, RAW_TOOL_PROTOCOL_RE
from app.core.config import Settings
from app.models.conversation import Conversation, MemorySummary, Message
from app.services.model_gateway import ModelGateway


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        gateway: ModelGateway,
        settings: Settings,
        user_id: UUID | None = None,
    ) -> None:
        self.session, self.gateway, self.settings, self.user_id = (
            session,
            gateway,
            settings,
            user_id,
        )

    async def update(self, conversation_id: UUID) -> bool:
        try:
            async with asyncio.timeout(self.settings.agent_memory_timeout_seconds):
                return await self._update(conversation_id)
        except asyncio.CancelledError:
            await self.session.rollback()
            raise
        except Exception:
            # Best effort: never lose the already committed assistant answer,
            # and never advance the cursor when compaction failed or timed out.
            await self.session.rollback()
            return False

    async def _update(self, conversation_id: UUID) -> bool:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == self.user_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        conversation = result.scalar_one_or_none()
        if not isinstance(conversation, Conversation):
            await self.session.rollback()
            return False
        metadata = dict(conversation.metadata_ or {})
        cursor = metadata.get("memory_cursor") or {}
        if not cursor:
            # Legacy summaries predate cursors. Respect prior explicit disabling
            # instead of resurrecting the same historical facts after upgrading.
            retired_at = await self.session.scalar(
                select(MemorySummary.valid_to)
                .where(
                    MemorySummary.conversation_id == conversation_id,
                    MemorySummary.valid_to.is_not(None),
                )
                .order_by(MemorySummary.valid_to.desc())
                .limit(1)
            )
            if retired_at is not None:
                cursor = {"after_time": retired_at.isoformat()}
        messages = await self._source_messages(conversation_id, cursor)
        batch, next_cursor = self._batch(messages, cursor)
        if not batch:
            await self.session.rollback()
            return False

        result = await self.session.execute(
            select(MemorySummary)
            .where(
                MemorySummary.conversation_id == conversation_id, MemorySummary.valid_to.is_(None)
            )
            .order_by(MemorySummary.updated_at.desc(), MemorySummary.id)
            .limit(1)
            .with_for_update()
        )
        existing = result.scalar_one_or_none()
        summary = await self.gateway.summarize_messages(
            batch,
            existing_summary=existing.summary[:8000] if existing else None,
        )
        if (
            not summary.strip()
            or len(summary) > 8000
            or RAW_TOOL_PROTOCOL_RE.search(summary)
            or any(marker in summary.lower() for marker in RAW_TOOL_PROTOCOL_MARKERS)
        ):
            await self.session.rollback()
            return False
        if existing:
            existing.summary = summary.strip()
        else:
            self.session.add(
                MemorySummary(
                    conversation_id=conversation_id,
                    summary=summary.strip(),
                    valid_from=messages[0].created_at,
                    valid_to=None,
                )
            )
        metadata["memory_cursor"] = next_cursor
        conversation.metadata_ = metadata
        await self.session.commit()
        return True

    async def _source_messages(self, conversation_id: UUID, cursor: dict) -> list[Message]:
        filters = (
            Message.conversation_id == conversation_id,
            Message.role.in_(("user", "assistant")),
            Message.content != "",
        )
        recent = (
            select(Message.id)
            .where(*filters)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(max(self.settings.agent_memory_message_limit, 0))
        )
        statement = select(Message).where(*filters, Message.id.not_in(recent))
        if cursor.get("after_time"):
            statement = statement.where(
                Message.created_at > datetime.fromisoformat(cursor["after_time"])
            )
        elif cursor:
            position = tuple_(Message.created_at, Message.id)
            saved = (datetime.fromisoformat(cursor["created_at"]), UUID(cursor["message_id"]))
            statement = statement.where(
                position >= saved if cursor.get("offset", 0) else position > saved
            )
        result = await self.session.execute(
            statement.order_by(Message.created_at, Message.id).limit(50)
        )
        return list(result.scalars().all())

    def _batch(self, messages: list[Message], cursor: dict) -> tuple[list[dict[str, str]], dict]:
        remaining = self.settings.agent_memory_input_max_chars
        batch: list[dict[str, str]] = []
        next_cursor = dict(cursor)
        for message in messages:
            offset = cursor.get("offset", 0) if str(message.id) == cursor.get("message_id") else 0
            content = message.content[offset : offset + remaining]
            if not content:
                break
            batch.append({"role": message.role, "content": content})
            end = offset + len(content)
            complete = end == len(message.content)
            next_cursor = {
                "version": 1,
                "message_id": str(message.id),
                "created_at": message.created_at.isoformat(),
                "offset": 0 if complete else end,
                "message_count": next_cursor.get("message_count", 0) + int(complete),
            }
            remaining -= len(content)
            if not remaining:
                break
        return batch, next_cursor

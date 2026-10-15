"""Durable, owner-bound approvals stored with their conversation trace.

An approval is consumed and committed before any tool side effect. An interrupted
continuation is never made pending again: the effect may already have happened.
"""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.schemas import AgentExecutionState, ToolConfirmationRequest


class ToolApprovalStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, state: AgentExecutionState, route, task_type: str, ttl_seconds: int
    ) -> None:
        approval_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        call = state.tool_calls[-1]
        call.update({
            "approval_id": str(approval_id),
            "approval_status": "pending",
            "approval_expires_at": expires_at.isoformat(),
        })
        self.session.add(Message(
            id=approval_id, conversation_id=state.conversation_id, role="assistant", content="",
            model_name=route.model_name,
            metadata_={
                "tool_calls": deepcopy(state.tool_calls), "trace_id": state.trace_id,
                "model_route": asdict(route), "citations": state.citations,
                "continuation": {
                    "citations": state.citations, "mcp_resources": state.mcp_resources,
                    "mcp_prompts": state.mcp_prompts,
                    "observations": list(state.observations[:-1]),
                    "tool_calls": deepcopy(state.tool_calls[:-1]),
                    "search_fetch_targets": deepcopy(state.search_fetch_targets),
                },
                "approval": {
                    "status": "pending", "expires_at": expires_at.isoformat(),
                    "tool_name": call["tool_name"], "arguments": deepcopy(call["arguments"]),
                    "message": state.message, "reason": call.get("reason", ""),
                    "task_type": task_type,
                },
            },
        ))
        await self.session.commit()

    async def _locked(
        self, conversation_id: UUID, user_id: UUID | None,
        approval_id: UUID | None = None, payload: ToolConfirmationRequest | None = None,
    ) -> Message:
        statement = (
            select(Message).join(Conversation)
            .where(Message.conversation_id == conversation_id, Conversation.user_id == user_id,
                   Message.role == "assistant", Message.metadata_["approval"].is_not(None))
        )
        if approval_id is not None:
            statement = statement.where(Message.id == approval_id)
        elif payload is not None:
            # Compatibility for old clients: only an exact, pending server-authored plan qualifies.
            approval = Message.metadata_["approval"]
            statement = statement.where(
                approval["status"].astext == "pending",
                approval["tool_name"].astext == payload.tool_name,
                approval["message"].astext == payload.message,
                approval["arguments"] == payload.arguments,
            ).order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
        else:
            raise HTTPException(status_code=409, detail="A persisted tool approval is required")
        result = await self.session.execute(
            statement.with_for_update(of=Message).execution_options(populate_existing=True)
        )
        message = result.scalar_one_or_none()
        if message is None or not isinstance((message.metadata_ or {}).get("approval"), dict):
            raise HTTPException(status_code=409, detail="Tool approval was not found or already resolved")
        return message

    async def claim(self, user_id: UUID | None, payload: ToolConfirmationRequest) -> Message:
        message = await self._locked(
            payload.conversation_id, user_id, payload.approval_id, payload,
        )
        approval = message.metadata_["approval"]
        if any((
            approval["tool_name"] != payload.tool_name,
            approval["arguments"] != payload.arguments,
            approval["message"] != payload.message,
            approval["task_type"] != payload.task_type,
        )):
            raise HTTPException(status_code=409, detail="Confirmation does not match the stored tool plan")
        await self._require_pending(message)
        await self.finish(message, "approved")
        return message

    async def cancel(self, user_id: UUID, conversation_id: UUID, approval_id: UUID) -> None:
        message = await self._locked(conversation_id, user_id, approval_id)
        if message.metadata_["approval"]["status"] == "cancelled":
            return
        await self._require_pending(message)
        await self.finish(message, "cancelled")

    async def _require_pending(self, message: Message) -> None:
        approval = message.metadata_["approval"]
        if approval["status"] != "pending":
            raise HTTPException(status_code=409, detail="Tool approval has already been resolved")
        if datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC):
            await self.finish(message, "expired")
            raise HTTPException(status_code=409, detail="Tool approval expired; send a new request")

    async def finish(self, message: Message, status: str, error: str | None = None) -> None:
        # Memory compaction may roll back a read transaction and expire attached
        # objects. Reload explicitly; attribute access cannot perform async IO.
        await self.session.refresh(message)
        metadata = deepcopy(message.metadata_)
        metadata["approval"].update({
            "status": status, "updated_at": datetime.now(UTC).isoformat(), "error": error,
        })
        for call in metadata.get("tool_calls", []):
            if call.get("approval_id") == str(message.id):
                call.update({"approval_status": status, "approval_error": error})
        message.metadata_ = metadata
        await self.session.commit()

    async def interrupt(self, message: Message) -> None:
        await self.session.rollback()
        await self.session.refresh(message)
        if message.metadata_["approval"]["status"] == "approved":
            await self.finish(message, "interrupted", (
                "Approved execution was interrupted. Check tool history before requesting it again; "
                "the tool may already have completed."
            ))

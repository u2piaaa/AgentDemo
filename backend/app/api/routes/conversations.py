from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.routes.auth import CurrentUser
from app.agent.runtime import AgentRuntime
from app.db.database import get_session
from app.models.conversation import Conversation, Message
from app.schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageRead,
    ToolConfirmationRequest,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=ConversationRead)
async def create_conversation(
    payload: ConversationCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = Conversation(title=payload.title, user_id=current_user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = await get_owned_conversation(session, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.title = payload.title.strip()
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> None:
    conversation = await get_owned_conversation(session, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await session.delete(conversation)
    await session.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    conversation = await get_owned_conversation(session, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    if payload.conversation_id is not None:
        conversation = await get_owned_conversation(session, payload.conversation_id, current_user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    runtime = AgentRuntime(
        session=session,
        plugin_registry=request.app.state.plugin_registry,
        user_id=current_user.id,
    )
    return EventSourceResponse(runtime.stream(payload))


@router.post("/chat/confirm/stream")
async def confirm_tool_stream(
    payload: ToolConfirmationRequest,
    request: Request,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    conversation = await get_owned_conversation(session, payload.conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    runtime = AgentRuntime(
        session=session,
        plugin_registry=request.app.state.plugin_registry,
        user_id=current_user.id,
    )
    return EventSourceResponse(runtime.stream_confirmed_tool(payload))


async def get_owned_conversation(
    session: AsyncSession, conversation_id: UUID, user_id: UUID
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()

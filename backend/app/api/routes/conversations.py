from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.runtime import AgentRuntime
from app.db.database import get_session
from app.models.conversation import Conversation, Message
from app.schemas import ChatRequest, ConversationCreate, ConversationRead, ConversationUpdate, MessageRead

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
async def list_conversations(session: AsyncSession = Depends(get_session)) -> list[Conversation]:
    result = await session.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=ConversationRead)
async def create_conversation(
    payload: ConversationCreate,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = Conversation(title=payload.title)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.title = payload.title.strip()
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
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
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    runtime = AgentRuntime(
        session=session,
        plugin_registry=request.app.state.plugin_registry,
    )
    return EventSourceResponse(runtime.stream(payload))

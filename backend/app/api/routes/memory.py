from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import CurrentUser
from app.db.database import get_session
from app.models.conversation import Conversation, MemorySummary
from app.schemas import MemorySummaryRead

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/summaries", response_model=list[MemorySummaryRead])
async def list_memory_summaries(
    current_user: CurrentUser,
    conversation_id: UUID | None = None,
    include_disabled: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[MemorySummary]:
    result = await session.execute(
        _owned_memory_statement(
            user_id=current_user.id,
            conversation_id=conversation_id,
            include_disabled=include_disabled,
        )
    )
    return list(result.scalars().all())


@router.get("/summaries/{summary_id}", response_model=MemorySummaryRead)
async def get_memory_summary(
    summary_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> MemorySummary:
    summary = await _get_owned_memory_summary(session, summary_id, current_user.id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Memory summary not found")
    return summary


@router.post("/summaries/{summary_id}/disable", response_model=MemorySummaryRead)
async def disable_memory_summary(
    summary_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> MemorySummary:
    summary = await _get_owned_memory_summary(session, summary_id, current_user.id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Memory summary not found")
    if summary.valid_to is None:
        summary.valid_to = datetime.now(UTC)
    await session.commit()
    await session.refresh(summary)
    return summary


@router.delete("/summaries/{summary_id}", status_code=204)
async def delete_memory_summary(
    summary_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> None:
    summary = await _get_owned_memory_summary(session, summary_id, current_user.id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Memory summary not found")
    await session.delete(summary)
    await session.commit()


async def _get_owned_memory_summary(
    session: AsyncSession,
    summary_id: UUID,
    user_id: UUID,
) -> MemorySummary | None:
    result = await session.execute(
        _owned_memory_statement(user_id=user_id, summary_id=summary_id, include_disabled=True)
    )
    return result.scalar_one_or_none()


def _owned_memory_statement(
    user_id: UUID,
    summary_id: UUID | None = None,
    conversation_id: UUID | None = None,
    include_disabled: bool = False,
) -> Select[tuple[MemorySummary]]:
    statement = (
        select(MemorySummary)
        .join(Conversation, Conversation.id == MemorySummary.conversation_id)
        .where(Conversation.user_id == user_id)
        .order_by(MemorySummary.updated_at.desc(), MemorySummary.created_at.desc())
    )
    if summary_id is not None:
        statement = statement.where(MemorySummary.id == summary_id)
    if conversation_id is not None:
        statement = statement.where(MemorySummary.conversation_id == conversation_id)
    if not include_disabled:
        statement = statement.where(MemorySummary.valid_to.is_(None))
    return statement

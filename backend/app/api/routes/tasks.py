from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import CurrentUser
from app.core.config import get_settings
from app.db.database import get_session
from app.models.conversation import Conversation
from app.models.task import (
    TASK_KIND_AGENT,
    TASK_KIND_MANUAL,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TERMINAL_TASK_STATUSES,
    Task,
    is_valid_task_status_transition,
)
from app.schemas import AgentTaskCreate, TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    current_user: CurrentUser,
    conversation_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[Task]:
    statement = select(Task).where(Task.user_id == current_user.id)
    if conversation_id is not None:
        conversation = await get_owned_conversation(session, conversation_id, current_user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        statement = statement.where(Task.conversation_id == conversation_id)
    result = await session.execute(
        statement.order_by(Task.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=TaskRead)
async def create_task(
    payload: TaskCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    if payload.conversation_id is not None:
        conversation = await get_owned_conversation(
            session, payload.conversation_id, current_user.id
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    task = Task(
        name=payload.name,
        kind=TASK_KIND_MANUAL,
        input_={},
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        status=TASK_STATUS_QUEUED,
        progress=0,
        attempt_count=0,
        max_attempts=1,
        trace_id=payload.trace_id,
        metadata_=payload.metadata,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/agent", response_model=TaskRead, status_code=202)
async def create_agent_task(
    payload: AgentTaskCreate,
    request: Request,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    if payload.conversation_id is not None:
        conversation = await get_owned_conversation(
            session, payload.conversation_id, current_user.id
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    prompt = payload.prompt.strip()
    idempotency_key = payload.idempotency_key.strip() if payload.idempotency_key else None
    if idempotency_key:
        existing = await get_task_by_idempotency_key(
            session, current_user.id, idempotency_key
        )
        if existing is not None:
            scheduler = getattr(request.app.state, "task_scheduler", None)
            if scheduler is not None and existing.status == TASK_STATUS_QUEUED:
                scheduler.enqueue(existing.id)
            return existing
    task = Task(
        name=(payload.name or _agent_task_name(prompt)).strip(),
        kind=TASK_KIND_AGENT,
        input_={"prompt": prompt},
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        status=TASK_STATUS_QUEUED,
        progress=0,
        idempotency_key=idempotency_key,
        attempt_count=0,
        max_attempts=payload.max_attempts or get_settings().agent_task_default_max_attempts,
        next_attempt_at=datetime.now(UTC),
        metadata_={"events": []},
    )
    session.add(task)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            existing = await get_task_by_idempotency_key(
                session, current_user.id, idempotency_key
            )
            if existing is not None:
                scheduler = getattr(request.app.state, "task_scheduler", None)
                if scheduler is not None and existing.status == TASK_STATUS_QUEUED:
                    scheduler.enqueue(existing.id)
                return existing
        raise
    await session.refresh(task)

    scheduler = getattr(request.app.state, "task_scheduler", None)
    if scheduler is not None:
        scheduler.enqueue(task.id)
    return task


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_owned_task(session, task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/events")
async def get_task_events(
    task_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    task = await get_owned_task(session, task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return list((task.metadata_ or {}).get("events") or [])


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_owned_task(session, task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.status is not None:
        validate_task_transition(task, payload.status)
        apply_task_status_timestamps(task, payload.status)
        task.status = payload.status
    if payload.progress is not None:
        task.progress = payload.progress
    if payload.error is not None:
        task.error = payload.error
    if payload.result is not None:
        task.result = payload.result
    if payload.trace_id is not None:
        task.trace_id = payload.trace_id
    if payload.metadata is not None:
        task.metadata_ = payload.metadata
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: UUID,
    request: Request,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_owned_task(session, task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    validate_task_transition(task, TASK_STATUS_CANCELLED)
    apply_task_status_timestamps(task, TASK_STATUS_CANCELLED)
    task.status = TASK_STATUS_CANCELLED
    await session.commit()
    await session.refresh(task)
    scheduler = getattr(request.app.state, "task_scheduler", None)
    if scheduler is not None:
        scheduler.cancel(task.id)
    return task


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


def validate_task_transition(task: Task, next_status: str) -> None:
    if is_valid_task_status_transition(task.status, next_status):
        return
    raise HTTPException(
        status_code=409,
        detail=f"Invalid task status transition from {task.status} to {next_status}",
    )


def apply_task_status_timestamps(task: Task, next_status: str) -> None:
    now = datetime.now(UTC)
    if next_status == TASK_STATUS_RUNNING and task.started_at is None:
        task.started_at = now
    if next_status in TERMINAL_TASK_STATUSES and task.finished_at is None:
        task.finished_at = now


async def get_owned_task(session: AsyncSession, task_id: UUID, user_id: UUID) -> Task | None:
    result = await session.execute(
        select(Task).where(
            Task.id == task_id,
            Task.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_task_by_idempotency_key(
    session: AsyncSession,
    user_id: UUID,
    idempotency_key: str,
) -> Task | None:
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def _agent_task_name(prompt: str) -> str:
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "Agent task")
    return first_line[:80]

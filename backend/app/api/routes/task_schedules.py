from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import CurrentUser
from app.api.routes.tasks import _agent_task_name, get_owned_conversation
from app.core.config import get_settings
from app.db.database import get_session
from app.models.task import Task
from app.models.task_schedule import TaskSchedule
from app.schemas import TaskRead, TaskScheduleCreate, TaskScheduleRead, TaskScheduleUpdate
from app.services.task_schedules import build_scheduled_task, initial_next_run

router = APIRouter(prefix="/task-schedules", tags=["task-schedules"])


@router.get("", response_model=list[TaskScheduleRead])
async def list_task_schedules(
    current_user: CurrentUser,
    conversation_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[TaskSchedule]:
    statement = select(TaskSchedule).where(TaskSchedule.user_id == current_user.id)
    if conversation_id is not None:
        conversation = await get_owned_conversation(session, conversation_id, current_user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        statement = statement.where(TaskSchedule.conversation_id == conversation_id)
    result = await session.execute(statement.order_by(TaskSchedule.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=TaskScheduleRead, status_code=201)
async def create_task_schedule(
    payload: TaskScheduleCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> TaskSchedule:
    if payload.conversation_id is not None:
        conversation = await get_owned_conversation(
            session, payload.conversation_id, current_user.id
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    prompt = payload.prompt.strip()
    interval_seconds = payload.interval_minutes * 60 if payload.interval_minutes else None
    try:
        next_run_at = initial_next_run(
            schedule_kind=payload.schedule_kind,
            timezone_name=payload.timezone,
            now=datetime.now(UTC),
            run_at=payload.run_at,
            interval_seconds=interval_seconds,
            daily_time=payload.daily_time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule = TaskSchedule(
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        name=(payload.name or _agent_task_name(prompt)).strip(),
        prompt=prompt,
        schedule_kind=payload.schedule_kind,
        timezone=payload.timezone.strip(),
        run_at=payload.run_at,
        interval_seconds=interval_seconds,
        daily_time=payload.daily_time,
        max_attempts=payload.max_attempts or get_settings().agent_task_default_max_attempts,
        next_run_at=next_run_at,
        enabled=True,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    return schedule


@router.patch("/{schedule_id}", response_model=TaskScheduleRead)
async def update_task_schedule(
    schedule_id: UUID,
    payload: TaskScheduleUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> TaskSchedule:
    schedule = await get_owned_schedule(session, schedule_id, current_user.id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    if payload.enabled and (
        schedule.next_run_at is None or schedule.next_run_at <= datetime.now(UTC)
    ):
        try:
            schedule.next_run_at = initial_next_run(
                schedule_kind=schedule.schedule_kind,
                timezone_name=schedule.timezone,
                now=datetime.now(UTC),
                run_at=schedule.run_at,
                interval_seconds=schedule.interval_seconds,
                daily_time=schedule.daily_time,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    schedule.enabled = payload.enabled
    await session.commit()
    await session.refresh(schedule)
    return schedule


@router.post("/{schedule_id}/run", response_model=TaskRead, status_code=202)
async def run_task_schedule_now(
    schedule_id: UUID,
    request: Request,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Task:
    schedule = await get_owned_schedule(session, schedule_id, current_user.id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    task = build_scheduled_task(schedule, occurrence=datetime.now(UTC), manual=True)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    scheduler = getattr(request.app.state, "task_scheduler", None)
    if scheduler is not None:
        scheduler.enqueue(task.id)
    return task


async def get_owned_schedule(
    session: AsyncSession, schedule_id: UUID, user_id: UUID
) -> TaskSchedule | None:
    result = await session.execute(
        select(TaskSchedule).where(
            TaskSchedule.id == schedule_id,
            TaskSchedule.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()

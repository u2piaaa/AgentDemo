from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.models.task import Task
from app.schemas import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead])
async def list_tasks(session: AsyncSession = Depends(get_session)) -> list[Task]:
    result = await session.execute(select(Task).order_by(Task.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=TaskRead)
async def create_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = Task(
        name=payload.name,
        conversation_id=payload.conversation_id,
        status="queued",
        progress=0,
        metadata_=payload.metadata,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: UUID, session: AsyncSession = Depends(get_session)) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.status is not None:
        task.status = payload.status
    if payload.progress is not None:
        task.progress = payload.progress
    if payload.error is not None:
        task.error = payload.error
    if payload.result is not None:
        task.result = payload.result
    if payload.metadata is not None:
        task.metadata_ = payload.metadata
    await session.commit()
    await session.refresh(task)
    return task

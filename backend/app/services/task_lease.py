"""Execution fencing shared by workers, graph persistence and the tool boundary.

The context is inherited by LangGraph's child tasks. The database token, not
process-local cancellation, is authoritative. Hold the row lock only until the
caller's next commit; never across a model or external tool call.
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


class TaskLeaseLost(asyncio.CancelledError):
    pass


@dataclass(frozen=True)
class TaskLease:
    task_id: UUID
    token: UUID


current_task_lease: ContextVar[TaskLease | None] = ContextVar("task_lease", default=None)
REPLAY_BLOCKED_ERROR = (
    "Execution stopped after a tool dispatch or saved answer. Automatic retry is disabled; "
    "review the conversation and tool history before starting a new task."
)


async def lock_task_lease(session: AsyncSession, *, live: bool = True) -> Task | None:
    lease = current_task_lease.get()
    if lease is None:
        return None
    task = await session.get(Task, lease.task_id, with_for_update=True, populate_existing=True)
    now = datetime.now(UTC)
    if (
        task is None or task.status != "running" or task.run_token != lease.token
        or (live and (task.lease_expires_at is None or task.lease_expires_at <= now))
        or (live and task.deadline_at is not None and task.deadline_at <= now)
    ):
        raise TaskLeaseLost("Background task no longer owns a live execution lease")
    return task


def block_replay(task: Task, reason: str) -> None:
    task.replay_blocked_at = task.replay_blocked_at or datetime.now(UTC)
    task.replay_blocked_reason = task.replay_blocked_reason or reason

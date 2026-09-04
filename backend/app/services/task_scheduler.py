import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.task import (
    TASK_KIND_AGENT,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STALE,
    Task,
)
from app.services.agent_task_runner import AgentTaskRunner
from app.services.task_schedules import dispatch_due_schedules

STALE_TASK_ERROR = "Task lease expired after the final permitted attempt"
RECOVERED_TASK_ERROR = "Task lease expired; execution was safely re-queued"
logger = logging.getLogger(__name__)


class TaskScheduler:
    """Run durable agent tasks with bounded concurrency and lease recovery."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        tool_registry=None,
        runner: AgentTaskRunner | None = None,
        *,
        max_concurrency: int | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.session_factory = session_factory
        self.runner = runner or AgentTaskRunner(session_factory, tool_registry)
        self.max_concurrency = max(
            max_concurrency or settings.agent_task_max_concurrency, 1
        )
        self.poll_seconds = max(poll_seconds or settings.task_schedule_poll_seconds, 1)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._jobs: dict[UUID, asyncio.Task[None]] = {}
        self._maintenance: asyncio.Task[None] | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._started = True
        self._maintenance = loop.create_task(self._maintenance_loop())

    def shutdown(self) -> None:
        if self._maintenance is not None:
            self._maintenance.cancel()
            self._maintenance = None
        for job in list(self._jobs.values()):
            job.cancel()
        self._jobs.clear()
        self._started = False

    def enqueue(self, task_id: UUID) -> bool:
        current = self._jobs.get(task_id)
        if current is not None and not current.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        job = loop.create_task(self._run_job(task_id))
        self._jobs[task_id] = job
        job.add_done_callback(
            lambda completed, queued_id=task_id: self._job_finished(queued_id, completed)
        )
        return True

    def cancel(self, task_id: UUID) -> bool:
        job = self._jobs.get(task_id)
        if job is None or job.done():
            return False
        job.cancel()
        return True

    async def recover_stale_running_tasks(self) -> int:
        async with self.session_factory() as session:
            return await recover_stale_running_tasks(session)

    async def _run_job(self, task_id: UUID) -> None:
        while True:
            async with self._semaphore:
                outcome = await self.runner.run(task_id)
            if outcome.retry_at is None:
                return
            delay = max((outcome.retry_at - datetime.now(UTC)).total_seconds(), 0)
            if delay:
                await asyncio.sleep(delay)

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                await self._recover_and_resume()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Task scheduler maintenance iteration failed")
            await asyncio.sleep(self.poll_seconds)

    async def _recover_and_resume(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            await recover_stale_running_tasks(session, now=now)
            scheduled_task_ids = await dispatch_due_schedules(session, now=now)
            result = await session.execute(
                select(Task.id).where(
                    Task.status == TASK_STATUS_QUEUED,
                    Task.kind == TASK_KIND_AGENT,
                    or_(Task.next_attempt_at.is_(None), Task.next_attempt_at <= now),
                )
            )
            ready_task_ids = list(result.scalars().all())
        for task_id in dict.fromkeys([*scheduled_task_ids, *ready_task_ids]):
            self.enqueue(task_id)

    def _job_finished(self, task_id: UUID, job: asyncio.Task[None]) -> None:
        self._jobs.pop(task_id, None)
        if job.cancelled():
            return
        error = job.exception()
        if error is not None:
            logger.error(
                "Background task worker exited unexpectedly",
                exc_info=(type(error), error, error.__traceback__),
                extra={"task_id": str(task_id)},
            )


async def recover_stale_running_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Recover only abandoned tasks whose execution lease has expired."""

    now_utc = now or datetime.now(UTC)
    result = await session.execute(select(Task).where(Task.status == TASK_STATUS_RUNNING))
    tasks = list(result.scalars().all())
    recovered = 0
    for task in tasks:
        lease_expires_at = getattr(task, "lease_expires_at", None)
        if lease_expires_at is not None and lease_expires_at > now_utc:
            continue
        attempt_count = getattr(task, "attempt_count", 1) or 1
        max_attempts = getattr(task, "max_attempts", 1) or 1
        if attempt_count < max_attempts:
            task.status = TASK_STATUS_QUEUED
            task.error = RECOVERED_TASK_ERROR
            task.finished_at = None
            task.next_attempt_at = now_utc
        else:
            task.status = TASK_STATUS_STALE
            task.finished_at = now_utc
            if task.error is None:
                task.error = STALE_TASK_ERROR
        task.lease_expires_at = None
        recovered += 1
    if recovered:
        await session.commit()
    return recovered

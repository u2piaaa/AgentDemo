import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.database import SessionLocal
from app.models.task import (
    TASK_KIND_AGENT,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STALE,
    Task,
)
from app.services.agent_task_runner import AgentTaskRunner

STALE_TASK_ERROR = "Task was running during service startup and marked stale"
logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        tool_registry=None,
        runner: AgentTaskRunner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.runner = runner or AgentTaskRunner(session_factory, tool_registry)
        self._jobs: dict[UUID, asyncio.Task[None]] = {}
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._started = True
        asyncio.create_task(self._recover_and_resume())

    def shutdown(self) -> None:
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
        job = loop.create_task(self.runner.run(task_id))
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

    async def _recover_and_resume(self) -> None:
        async with self.session_factory() as session:
            await recover_stale_running_tasks(session)
            result = await session.execute(
                select(Task.id).where(
                    Task.status == TASK_STATUS_QUEUED,
                    Task.kind == TASK_KIND_AGENT,
                )
            )
            task_ids = list(result.scalars().all())
        for task_id in task_ids:
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


async def recover_stale_running_tasks(session: AsyncSession) -> int:
    result = await session.execute(select(Task).where(Task.status == TASK_STATUS_RUNNING))
    tasks = list(result.scalars().all())
    for task in tasks:
        task.status = TASK_STATUS_STALE
        task.finished_at = datetime.now(UTC)
        if task.error is None:
            task.error = STALE_TASK_ERROR
    if tasks:
        await session.commit()
    return len(tasks)

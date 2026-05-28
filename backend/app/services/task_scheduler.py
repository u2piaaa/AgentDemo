import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.database import SessionLocal
from app.models.task import TASK_STATUS_RUNNING, TASK_STATUS_STALE, Task

STALE_TASK_ERROR = "Task was running during service startup and marked stale"


class TaskScheduler:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        if not self.scheduler.running:
            self._schedule_running_task_recovery()
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _schedule_running_task_recovery(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(self.recover_stale_running_tasks())

    async def recover_stale_running_tasks(self) -> int:
        async with self.session_factory() as session:
            return await recover_stale_running_tasks(session)


async def recover_stale_running_tasks(session: AsyncSession) -> int:
    result = await session.execute(select(Task).where(Task.status == TASK_STATUS_RUNNING))
    tasks = list(result.scalars().all())
    for task in tasks:
        task.status = TASK_STATUS_STALE
        if task.error is None:
            task.error = STALE_TASK_ERROR
    if tasks:
        await session.commit()
    return len(tasks)

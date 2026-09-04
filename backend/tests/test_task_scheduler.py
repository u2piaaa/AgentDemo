import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.agent_task_runner import TaskRunOutcome
from app.services.task_scheduler import (
    RECOVERED_TASK_ERROR,
    STALE_TASK_ERROR,
    TaskScheduler,
    recover_stale_running_tasks,
)


class FakeResult:
    def __init__(self, items) -> None:
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items


class FakeSession:
    def __init__(self, tasks) -> None:
        self.tasks = tasks
        self.committed = False

    async def execute(self, statement):
        assert statement_filters_value(statement, "tasks.status", "running")
        return FakeResult([task for task in self.tasks if task.status == "running"])

    async def commit(self) -> None:
        self.committed = True


def make_task(status: str, error: str | None = None):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        error=error,
        attempt_count=1,
        max_attempts=1,
        lease_expires_at=None,
    )


def statement_filters_value(statement, column_name: str, value: str) -> bool:
    return any(
        str(getattr(criteria, "left", "")) == column_name
        and getattr(getattr(criteria, "right", None), "value", None) == value
        for criteria in statement._where_criteria
    )


@pytest.mark.asyncio
async def test_recover_stale_running_tasks_marks_running_stale() -> None:
    running = make_task("running")
    session = FakeSession([running])

    recovered = await recover_stale_running_tasks(session)  # type: ignore[arg-type]

    assert recovered == 1
    assert running.status == "stale"
    assert running.error == STALE_TASK_ERROR
    assert session.committed is True


@pytest.mark.asyncio
async def test_recover_stale_running_tasks_preserves_terminal_tasks() -> None:
    succeeded = make_task("succeeded")
    failed = make_task("failed", error="boom")
    cancelled = make_task("cancelled")
    session = FakeSession([succeeded, failed, cancelled])

    recovered = await recover_stale_running_tasks(session)  # type: ignore[arg-type]

    assert recovered == 0
    assert succeeded.status == "succeeded"
    assert failed.status == "failed"
    assert failed.error == "boom"
    assert cancelled.status == "cancelled"
    assert session.committed is False


@pytest.mark.asyncio
async def test_recovery_preserves_task_with_live_lease() -> None:
    running = make_task("running")
    running.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    session = FakeSession([running])

    recovered = await recover_stale_running_tasks(session)  # type: ignore[arg-type]

    assert recovered == 0
    assert running.status == "running"
    assert session.committed is False


@pytest.mark.asyncio
async def test_recovery_requeues_expired_task_when_attempts_remain() -> None:
    running = make_task("running")
    running.max_attempts = 3
    running.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session = FakeSession([running])

    recovered = await recover_stale_running_tasks(session)  # type: ignore[arg-type]

    assert recovered == 1
    assert running.status == "queued"
    assert running.error == RECOVERED_TASK_ERROR
    assert running.next_attempt_at is not None
    assert session.committed is True


class WaitingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, task_id) -> None:
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_scheduler_prevents_duplicate_jobs_and_cancels_running_job() -> None:
    runner = WaitingRunner()
    scheduler = TaskScheduler(runner=runner)  # type: ignore[arg-type]
    task_id = uuid4()

    assert scheduler.enqueue(task_id) is True
    await runner.started.wait()
    assert scheduler.enqueue(task_id) is False
    assert scheduler.cancel(task_id) is True
    await asyncio.sleep(0)
    assert scheduler.cancel(task_id) is False


def test_scheduler_start_without_event_loop_can_retry_later() -> None:
    scheduler = TaskScheduler(runner=WaitingRunner())  # type: ignore[arg-type]

    scheduler.start()

    assert scheduler._started is False


class ConcurrencyRunner:
    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, task_id) -> TaskRunOutcome:
        self.current += 1
        self.peak = max(self.peak, self.current)
        if self.peak == 2:
            self.started.set()
        await self.release.wait()
        self.current -= 1
        return TaskRunOutcome()


@pytest.mark.asyncio
async def test_scheduler_enforces_global_concurrency_limit() -> None:
    runner = ConcurrencyRunner()
    scheduler = TaskScheduler(runner=runner, max_concurrency=2)  # type: ignore[arg-type]
    for _ in range(3):
        scheduler.enqueue(uuid4())

    await asyncio.wait_for(runner.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert runner.peak == 2

    jobs = list(scheduler._jobs.values())
    runner.release.set()
    await asyncio.gather(*jobs)
    assert runner.peak == 2


class RetryOrderRunner:
    def __init__(self, retrying_task) -> None:
        self.retrying_task = retrying_task
        self.calls: list = []

    async def run(self, task_id) -> TaskRunOutcome:
        self.calls.append(task_id)
        if task_id == self.retrying_task and self.calls.count(task_id) == 1:
            return TaskRunOutcome(retry_at=datetime.now(UTC) + timedelta(milliseconds=30))
        return TaskRunOutcome()


@pytest.mark.asyncio
async def test_retry_backoff_does_not_hold_concurrency_slot() -> None:
    first = uuid4()
    second = uuid4()
    runner = RetryOrderRunner(first)
    scheduler = TaskScheduler(runner=runner, max_concurrency=1)  # type: ignore[arg-type]

    scheduler.enqueue(first)
    scheduler.enqueue(second)
    await asyncio.gather(*list(scheduler._jobs.values()))

    assert runner.calls == [first, second, first]

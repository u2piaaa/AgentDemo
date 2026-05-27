from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.task_scheduler import STALE_TASK_ERROR, recover_stale_running_tasks


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
    return SimpleNamespace(id=uuid4(), status=status, error=error)


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

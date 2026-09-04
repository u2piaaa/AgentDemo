from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.task_schedules import (
    build_scheduled_task,
    dispatch_due_schedules,
    initial_next_run,
    next_run_after_occurrence,
    validate_timezone,
)


class FakeResult:
    def __init__(self, items) -> None:
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items


class FakeSession:
    def __init__(self, schedules) -> None:
        self.schedules = schedules
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return FakeResult(self.schedules)

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        for item in self.added:
            if item.id is None:
                item.id = uuid4()

    async def commit(self) -> None:
        self.commits += 1


def test_one_time_schedule_requires_future_offset_datetime() -> None:
    now = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="future"):
        initial_next_run(
            schedule_kind="once",
            timezone_name="UTC",
            now=now,
            run_at=now,
        )
    with pytest.raises(ValueError, match="timezone offset"):
        initial_next_run(
            schedule_kind="once",
            timezone_name="UTC",
            now=now,
            run_at=datetime(2026, 9, 5, 2, 0),
        )


def test_daily_schedule_honors_iana_timezone() -> None:
    now = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)

    next_run = initial_next_run(
        schedule_kind="daily",
        timezone_name="Asia/Hong_Kong",
        now=now,
        daily_time="11:30",
    )

    assert next_run == datetime(2026, 9, 4, 3, 30, tzinfo=UTC)


def test_interval_schedule_skips_missed_occurrences_without_bursting() -> None:
    due = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
    schedule = SimpleNamespace(
        schedule_kind="interval",
        interval_seconds=300,
        next_run_at=due,
    )

    next_run = next_run_after_occurrence(
        schedule, now=due + timedelta(minutes=17)
    )

    assert next_run == datetime(2026, 9, 4, 1, 20, tzinfo=UTC)


def test_scheduled_task_has_deterministic_occurrence_key_and_retry_policy() -> None:
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        name="Daily research",
        prompt="Summarize project activity",
        schedule_kind="daily",
        max_attempts=4,
    )
    occurrence = datetime(2026, 9, 4, 3, 30, tzinfo=UTC)

    first = build_scheduled_task(schedule, occurrence=occurrence)
    second = build_scheduled_task(schedule, occurrence=occurrence)

    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key == f"schedule:{schedule.id}:{occurrence.isoformat()}"
    assert first.max_attempts == 4
    assert first.schedule_id == schedule.id
    assert first.metadata_["schedule"]["scheduled_for"] == occurrence.isoformat()


def test_timezone_validation_rejects_unknown_zone() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        validate_timezone("Mars/Olympus_Mons")


@pytest.mark.asyncio
async def test_dispatch_due_one_time_schedule_atomically_disables_it() -> None:
    occurrence = datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        name="One-time report",
        prompt="Prepare the report",
        schedule_kind="once",
        timezone="UTC",
        interval_seconds=None,
        daily_time=None,
        max_attempts=3,
        next_run_at=occurrence,
        last_run_at=None,
        last_task_id=None,
        enabled=True,
    )
    session = FakeSession([schedule])

    task_ids = await dispatch_due_schedules(
        session,  # type: ignore[arg-type]
        now=occurrence + timedelta(seconds=1),
    )

    assert task_ids == [session.added[0].id]
    assert schedule.enabled is False
    assert schedule.next_run_at is None
    assert schedule.last_run_at == occurrence
    assert schedule.last_task_id == session.added[0].id
    assert session.commits == 1


@pytest.mark.asyncio
async def test_dispatch_due_interval_schedule_advances_without_duplicate_burst() -> None:
    occurrence = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=None,
        name="Interval report",
        prompt="Prepare the report",
        schedule_kind="interval",
        timezone="UTC",
        interval_seconds=300,
        daily_time=None,
        max_attempts=2,
        next_run_at=occurrence,
        last_run_at=None,
        last_task_id=None,
        enabled=True,
    )
    session = FakeSession([schedule])

    await dispatch_due_schedules(
        session,  # type: ignore[arg-type]
        now=occurrence + timedelta(minutes=17),
    )

    assert len(session.added) == 1
    assert schedule.enabled is True
    assert schedule.next_run_at == occurrence + timedelta(minutes=20)

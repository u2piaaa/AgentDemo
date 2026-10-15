from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TASK_KIND_AGENT, TASK_STATUS_QUEUED, Task
from app.models.task_schedule import (
    SCHEDULE_KIND_DAILY,
    SCHEDULE_KIND_INTERVAL,
    SCHEDULE_KIND_ONCE,
    TaskSchedule,
)

MAX_DUE_SCHEDULES_PER_TICK = 100


def validate_timezone(timezone_name: str) -> str:
    clean_name = timezone_name.strip()
    try:
        ZoneInfo(clean_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Unknown IANA timezone: {clean_name}") from exc
    return clean_name


def initial_next_run(
    *,
    schedule_kind: str,
    timezone_name: str,
    now: datetime,
    run_at: datetime | None = None,
    interval_seconds: int | None = None,
    daily_time: str | None = None,
) -> datetime:
    now_utc = _as_utc(now)
    timezone_name = validate_timezone(timezone_name)
    if schedule_kind == SCHEDULE_KIND_ONCE:
        if run_at is None:
            raise ValueError("run_at is required for a one-time schedule")
        run_at_utc = _as_utc(run_at)
        if run_at_utc <= now_utc:
            raise ValueError("run_at must be in the future")
        return run_at_utc
    if schedule_kind == SCHEDULE_KIND_INTERVAL:
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval_minutes must be at least 1")
        if run_at is not None and _as_utc(run_at) > now_utc:
            return _as_utc(run_at)
        return now_utc + timedelta(seconds=interval_seconds)
    if schedule_kind == SCHEDULE_KIND_DAILY:
        return _next_daily_run(now_utc, timezone_name, daily_time)
    raise ValueError(f"Unsupported schedule kind: {schedule_kind}")


def next_run_after_occurrence(schedule: TaskSchedule, *, now: datetime) -> datetime | None:
    now_utc = _as_utc(now)
    if schedule.schedule_kind == SCHEDULE_KIND_ONCE:
        return None
    if schedule.schedule_kind == SCHEDULE_KIND_INTERVAL:
        interval_seconds = schedule.interval_seconds or 0
        if interval_seconds < 60:
            raise ValueError("Stored interval_seconds must be at least 60")
        candidate = _as_utc(schedule.next_run_at or now_utc) + timedelta(
            seconds=interval_seconds
        )
        if candidate <= now_utc:
            missed_intervals = int((now_utc - candidate).total_seconds() // interval_seconds) + 1
            candidate += timedelta(seconds=missed_intervals * interval_seconds)
        return candidate
    if schedule.schedule_kind == SCHEDULE_KIND_DAILY:
        return _next_daily_run(now_utc, schedule.timezone, schedule.daily_time)
    raise ValueError(f"Unsupported schedule kind: {schedule.schedule_kind}")


async def dispatch_due_schedules(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[UUID]:
    now_utc = _as_utc(now or datetime.now(UTC))
    result = await session.execute(
        select(TaskSchedule)
        .where(
            TaskSchedule.enabled.is_(True),
            TaskSchedule.next_run_at.is_not(None),
            TaskSchedule.next_run_at <= now_utc,
        )
        .order_by(TaskSchedule.next_run_at.asc())
        .limit(MAX_DUE_SCHEDULES_PER_TICK)
        .with_for_update(skip_locked=True)
    )
    schedules = list(result.scalars().all())
    task_ids: list[UUID] = []
    for schedule in schedules:
        schedule.last_dispatch_at = now_utc
        schedule.last_dispatch_error = None
        try:
            occurrence = latest_due_occurrence(schedule, now=now_utc)
            next_run = next_run_after_occurrence(schedule, now=now_utc)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            schedule.enabled = False
            schedule.last_dispatch_status = "invalid"
            schedule.last_dispatch_error = str(exc)
            continue
        schedule.next_run_at = next_run
        if next_run is None:
            schedule.enabled = False
        if (now_utc - occurrence).total_seconds() > schedule.misfire_grace_seconds:
            schedule.last_dispatch_status = "missed"
            continue
        if await active_schedule_task(session, schedule.id) is not None:
            schedule.last_dispatch_status = "overlap_skipped"
            continue
        task = build_scheduled_task(schedule, occurrence=occurrence)
        session.add(task)
        await session.flush()
        schedule.last_run_at = occurrence
        schedule.last_task_id = task.id
        schedule.last_dispatch_status = "queued"
        task_ids.append(task.id)
    if schedules:
        await session.commit()
    return task_ids


async def active_schedule_task(session: AsyncSession, schedule_id: UUID) -> Task | None:
    # Caller holds the schedule row lock, serializing manual and timed dispatch.
    result = await session.execute(select(Task).where(
        Task.schedule_id == schedule_id, Task.status.in_(("queued", "running")),
    ).order_by(Task.created_at).limit(1))
    return result.scalar_one_or_none()


def latest_due_occurrence(schedule: TaskSchedule, *, now: datetime) -> datetime:
    occurrence = _as_utc(schedule.next_run_at or now)
    if schedule.schedule_kind == SCHEDULE_KIND_INTERVAL:
        interval = schedule.interval_seconds or 0
        if interval < 60:
            raise ValueError("Stored interval_seconds must be at least 60")
        occurrence += timedelta(seconds=int((now - occurrence).total_seconds() // interval) * interval)
    elif schedule.schedule_kind == SCHEDULE_KIND_DAILY:
        candidate = _next_daily_run(now - timedelta(days=3), schedule.timezone, schedule.daily_time)
        while candidate <= now:
            occurrence = max(occurrence, candidate)
            candidate = _next_daily_run(candidate, schedule.timezone, schedule.daily_time)
    return occurrence


def build_scheduled_task(
    schedule: TaskSchedule,
    *,
    occurrence: datetime,
    manual: bool = False,
) -> Task:
    occurrence_utc = _as_utc(occurrence)
    occurrence_key = uuid4().hex if manual else occurrence_utc.isoformat()
    return Task(
        name=schedule.name,
        kind=TASK_KIND_AGENT,
        input_={"prompt": schedule.prompt},
        user_id=schedule.user_id,
        conversation_id=schedule.conversation_id,
        schedule_id=schedule.id,
        idempotency_key=f"schedule:{schedule.id}:{occurrence_key}",
        status=TASK_STATUS_QUEUED,
        progress=0,
        attempt_count=0,
        max_attempts=schedule.max_attempts,
        next_attempt_at=occurrence_utc,
        timeout_seconds=180,
        deadline_at=occurrence_utc + timedelta(seconds=900),
        metadata_={
            "events": [],
            "schedule": {
                "schedule_id": str(schedule.id),
                "schedule_kind": schedule.schedule_kind,
                "scheduled_for": occurrence_utc.isoformat(),
                "manual": manual,
            },
        },
    )


def _next_daily_run(now_utc: datetime, timezone_name: str, daily_time: str | None) -> datetime:
    if daily_time is None:
        raise ValueError("daily_time is required for a daily schedule")
    try:
        hour_text, minute_text = daily_time.split(":", 1)
        local_time = time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError) as exc:
        raise ValueError("daily_time must use HH:MM in 24-hour time") from exc
    zone = ZoneInfo(validate_timezone(timezone_name))
    local_now = now_utc.astimezone(zone)
    candidate_date: date = local_now.date()
    for offset in range(370):
        # Fold=0 chooses the first occurrence; never execute twice on a fall-back day.
        naive = datetime.combine(candidate_date + timedelta(days=offset), local_time)
        candidate = naive.replace(tzinfo=zone, fold=0).astimezone(UTC)
        # Nonexistent wall times (spring-forward or a skipped date) do not round-trip.
        if candidate.astimezone(zone).replace(tzinfo=None) != naive:
            continue
        if candidate > now_utc:
            return candidate
    raise ValueError("No valid daily occurrence in the next year")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Scheduled datetimes must include a timezone offset")
    return value.astimezone(UTC)

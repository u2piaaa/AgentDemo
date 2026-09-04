from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import task_schedules
from app.api.routes.auth import get_current_user
from app.db.database import get_session
from app.schemas import TaskScheduleCreate


class FakeResult:
    def __init__(self, *, items=None, scalar=None) -> None:
        self.items = items or []
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.items

    def scalar_one_or_none(self):
        return self.scalar


class FakeSession:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return self.results.pop(0)

    def add(self, item) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, item) -> None:
        now = datetime.now(UTC)
        if item.id is None:
            item.id = uuid4()
        if item.created_at is None:
            item.created_at = now
        if item.updated_at is None:
            item.updated_at = now


class FakeScheduler:
    def __init__(self) -> None:
        self.enqueued = []

    def enqueue(self, task_id) -> bool:
        self.enqueued.append(task_id)
        return True


def make_schedule(user_id, *, enabled: bool = True):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        conversation_id=None,
        name="Daily research",
        prompt="Summarize project activity",
        schedule_kind="daily",
        timezone="Asia/Hong_Kong",
        run_at=None,
        interval_seconds=None,
        daily_time="09:00",
        max_attempts=3,
        next_run_at=now + timedelta(hours=1),
        last_run_at=None,
        last_task_id=None,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def make_client(session: FakeSession, user=None, scheduler=None) -> TestClient:
    app = FastAPI()
    if scheduler is not None:
        app.state.task_scheduler = scheduler
    app.include_router(task_schedules.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: session
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_schedule_routes_require_login() -> None:
    response = make_client(FakeSession()).get("/api/task-schedules")

    assert response.status_code == 401


def test_schedule_payload_requires_kind_specific_fields() -> None:
    for kind, message in (
        ("once", "run_at"),
        ("interval", "interval_minutes"),
        ("daily", "daily_time"),
    ):
        try:
            TaskScheduleCreate(prompt="Run later", schedule_kind=kind)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"{kind} schedule unexpectedly validated")


def test_create_interval_schedule_calculates_next_run() -> None:
    user = SimpleNamespace(id=uuid4())
    session = FakeSession()
    client = make_client(session, user)

    response = client.post(
        "/api/task-schedules",
        json={
            "prompt": "Summarize project activity",
            "schedule_kind": "interval",
            "interval_minutes": 15,
            "timezone": "UTC",
            "max_attempts": 4,
        },
    )

    assert response.status_code == 201
    assert response.json()["interval_seconds"] == 900
    assert response.json()["max_attempts"] == 4
    assert response.json()["next_run_at"] is not None
    assert session.added[0].user_id == user.id


def test_create_schedule_returns_actionable_validation_error() -> None:
    user = SimpleNamespace(id=uuid4())
    client = make_client(FakeSession(), user)

    response = client.post(
        "/api/task-schedules",
        json={
            "prompt": "Run later",
            "schedule_kind": "once",
            "run_at": "2020-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 422
    assert "future" in response.json()["detail"]


def test_pause_schedule_updates_owned_record() -> None:
    user = SimpleNamespace(id=uuid4())
    schedule = make_schedule(user.id)
    session = FakeSession([FakeResult(scalar=schedule)])
    client = make_client(session, user)

    response = client.patch(
        f"/api/task-schedules/{schedule.id}", json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert schedule.enabled is False


def test_run_schedule_now_creates_linked_agent_task() -> None:
    user = SimpleNamespace(id=uuid4())
    schedule = make_schedule(user.id)
    session = FakeSession([FakeResult(scalar=schedule)])
    scheduler = FakeScheduler()
    client = make_client(session, user, scheduler)

    response = client.post(f"/api/task-schedules/{schedule.id}/run")

    assert response.status_code == 202
    task = session.added[0]
    assert task.schedule_id == schedule.id
    assert task.input_ == {"prompt": schedule.prompt}
    assert task.metadata_["schedule"]["manual"] is True
    assert scheduler.enqueued == [task.id]

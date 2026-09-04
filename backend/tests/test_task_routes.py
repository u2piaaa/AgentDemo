from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import tasks
from app.api.routes.auth import get_current_user
from app.db.database import get_session
from app.schemas import AgentTaskCreate, TaskCreate, TaskUpdate


class FakeResult:
    def __init__(self, items=None, scalar=None) -> None:
        self.items = items or []
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.items

    def scalar_one_or_none(self):
        return self.scalar


class FakeSession:
    def __init__(self, results=None, expected_filters=None) -> None:
        self.results = list(results or [])
        self.expected_filters = list(expected_filters or [])
        self.added = None
        self.committed = False

    async def execute(self, statement):
        if self.expected_filters:
            column_name, value = self.expected_filters.pop(0)
            assert statement_filters_value(statement, column_name, value)
        return self.results.pop(0)

    def add(self, item) -> None:
        self.added = item

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, item) -> None:
        if getattr(item, "id", None) is None:
            item.id = uuid4()
        if getattr(item, "created_at", None) is None:
            item.created_at = datetime.now(UTC)


def statement_filters_value(statement, column_name: str, value: UUID) -> bool:
    return any(
        str(getattr(criteria, "left", "")) == column_name
        and getattr(getattr(criteria, "right", None), "value", None) == value
        for criteria in statement._where_criteria
    )


def make_task(user_id: UUID, conversation_id: UUID | None = None):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        conversation_id=conversation_id,
        name="Index document",
        status="queued",
        progress=0,
        error=None,
        result=None,
        trace_id=None,
        metadata_={},
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
    )


def make_client(session: FakeSession, user=None, scheduler=None) -> TestClient:
    app = FastAPI()
    if scheduler is not None:
        app.state.task_scheduler = scheduler
    app.include_router(tasks.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: session
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_task_payload_defaults() -> None:
    payload = TaskCreate(name="Index document")

    assert payload.conversation_id is None
    assert payload.trace_id is None
    assert payload.metadata == {}


def test_agent_task_payload_defaults() -> None:
    payload = AgentTaskCreate(prompt="Run a background analysis")

    assert payload.name is None
    assert payload.conversation_id is None
    assert payload.idempotency_key is None
    assert payload.max_attempts is None


def test_agent_task_payload_rejects_whitespace_prompt_and_key() -> None:
    with pytest.raises(ValueError, match="non-whitespace"):
        AgentTaskCreate(prompt="   ")
    with pytest.raises(ValueError, match="non-whitespace characters"):
        AgentTaskCreate(prompt="Analyze", idempotency_key="        ")


def test_task_update_accepts_progress() -> None:
    payload = TaskUpdate(status="running", progress=50, trace_id="trace-1")

    assert payload.status == "running"
    assert payload.progress == 50
    assert payload.trace_id == "trace-1"


def test_task_update_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        TaskUpdate(status="waiting")


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("GET", "/api/tasks", None),
        ("POST", "/api/tasks", {"name": "Index document"}),
        ("POST", "/api/tasks/agent", {"prompt": "Run a background analysis"}),
        ("GET", f"/api/tasks/{uuid4()}", None),
        ("PATCH", f"/api/tasks/{uuid4()}", {"progress": 20}),
        ("POST", f"/api/tasks/{uuid4()}/cancel", None),
    ],
)
def test_task_routes_require_login(method: str, path: str, json_body: dict | None) -> None:
    client = make_client(FakeSession())

    response = client.request(method, path, json=json_body)

    assert response.status_code == 401


def test_list_tasks_filters_to_current_user() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    session = FakeSession(
        results=[FakeResult(items=[task])],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.get("/api/tasks")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(task.id)]


def test_list_tasks_filters_to_owned_conversation() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    conversation = SimpleNamespace(id=conversation_id, user_id=user.id)
    task = make_task(user.id, conversation_id)
    session = FakeSession(
        results=[FakeResult(scalar=conversation), FakeResult(items=[task])],
        expected_filters=[
            ("conversations.user_id", user.id),
            ("tasks.conversation_id", conversation_id),
        ],
    )
    client = make_client(session, user)

    response = client.get(f"/api/tasks?conversation_id={conversation_id}")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(task.id)]


def test_list_tasks_rejects_unowned_conversation() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    session = FakeSession(
        results=[FakeResult(scalar=None)],
        expected_filters=[("conversations.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.get(f"/api/tasks?conversation_id={conversation_id}")

    assert response.status_code == 404


def test_create_task_requires_owned_conversation() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    session = FakeSession(
        results=[FakeResult(scalar=None)],
        expected_filters=[("conversations.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.post(
        "/api/tasks",
        json={"name": "Index document", "conversation_id": str(conversation_id)},
    )

    assert response.status_code == 404
    assert session.added is None


def test_create_task_assigns_current_user() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    conversation = SimpleNamespace(id=conversation_id, user_id=user.id)
    session = FakeSession(
        results=[FakeResult(scalar=conversation)],
        expected_filters=[("conversations.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.post(
        "/api/tasks",
        json={"name": "Index document", "conversation_id": str(conversation_id)},
    )

    assert response.status_code == 200
    assert session.added.user_id == user.id
    assert session.added.conversation_id == conversation_id


def test_create_task_accepts_trace_id() -> None:
    user = SimpleNamespace(id=uuid4())
    session = FakeSession()
    client = make_client(session, user)

    response = client.post(
        "/api/tasks",
        json={"name": "Index document", "trace_id": "trace-create"},
    )

    assert response.status_code == 200
    assert session.added.trace_id == "trace-create"


class FakeScheduler:
    def __init__(self) -> None:
        self.enqueued = []
        self.cancelled = []

    def enqueue(self, task_id) -> bool:
        self.enqueued.append(task_id)
        return True

    def cancel(self, task_id) -> bool:
        self.cancelled.append(task_id)
        return True


def test_create_agent_task_enqueues_owned_conversation() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    conversation = SimpleNamespace(id=conversation_id, user_id=user.id)
    session = FakeSession(
        results=[FakeResult(scalar=conversation)],
        expected_filters=[("conversations.user_id", user.id)],
    )
    scheduler = FakeScheduler()
    client = make_client(session, user, scheduler)

    response = client.post(
        "/api/tasks/agent",
        json={"prompt": "Analyze the current project", "conversation_id": str(conversation_id)},
    )

    assert response.status_code == 202
    assert session.added.kind == "agent"
    assert session.added.input_ == {"prompt": "Analyze the current project"}
    assert session.added.user_id == user.id
    assert scheduler.enqueued == [session.added.id]
    assert session.added.max_attempts == 3
    assert session.added.next_attempt_at is not None


def test_create_agent_task_returns_existing_idempotent_task() -> None:
    user = SimpleNamespace(id=uuid4())
    existing = make_task(user.id)
    existing.kind = "agent"
    existing.input_ = {"prompt": "Analyze the current project"}
    existing.idempotency_key = "request-12345678"
    session = FakeSession(results=[FakeResult(scalar=existing)])
    scheduler = FakeScheduler()
    client = make_client(session, user, scheduler)

    response = client.post(
        "/api/tasks/agent",
        json={
            "prompt": "Analyze the current project",
            "idempotency_key": "request-12345678",
        },
    )

    assert response.status_code == 202
    assert response.json()["id"] == str(existing.id)
    assert session.added is None
    assert scheduler.enqueued == [existing.id]


def test_get_task_rejects_other_users_task() -> None:
    user = SimpleNamespace(id=uuid4())
    session = FakeSession(
        results=[FakeResult(scalar=None)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.get(f"/api/tasks/{uuid4()}")

    assert response.status_code == 404


def test_update_task_rejects_other_users_task() -> None:
    user = SimpleNamespace(id=uuid4())
    session = FakeSession(
        results=[FakeResult(scalar=None)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.patch(f"/api/tasks/{uuid4()}", json={"progress": 20})

    assert response.status_code == 404


def test_update_task_allows_owner() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    session = FakeSession(
        results=[FakeResult(scalar=task)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.patch(
        f"/api/tasks/{task.id}",
        json={
            "status": "running",
            "progress": 20,
            "result": {"ok": True},
            "error": "partial warning",
            "trace_id": "trace-update",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["progress"] == 20
    assert response.json()["result"] == {"ok": True}
    assert response.json()["error"] == "partial warning"
    assert response.json()["trace_id"] == "trace-update"
    assert task.status == "running"
    assert task.progress == 20
    assert task.result == {"ok": True}
    assert task.error == "partial warning"
    assert task.trace_id == "trace-update"
    assert task.started_at is not None
    assert task.finished_at is None


def test_update_task_sets_finished_at_for_terminal_status() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    task.status = "running"
    session = FakeSession(
        results=[FakeResult(scalar=task)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.patch(f"/api/tasks/{task.id}", json={"status": "succeeded"})

    assert response.status_code == 200
    assert task.finished_at is not None


def test_update_task_rejects_invalid_status_transition() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    task.status = "succeeded"
    session = FakeSession(
        results=[FakeResult(scalar=task)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.patch(f"/api/tasks/{task.id}", json={"status": "running"})

    assert response.status_code == 409
    assert task.status == "succeeded"


def test_cancel_task_allows_owner() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    session = FakeSession(
        results=[FakeResult(scalar=task)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    scheduler = FakeScheduler()
    client = make_client(session, user, scheduler)

    response = client.post(f"/api/tasks/{task.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert task.status == "cancelled"
    assert task.finished_at is not None
    assert session.committed is True
    assert scheduler.cancelled == [task.id]


def test_cancel_task_rejects_other_users_task() -> None:
    user = SimpleNamespace(id=uuid4())
    session = FakeSession(
        results=[FakeResult(scalar=None)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.post(f"/api/tasks/{uuid4()}/cancel")

    assert response.status_code == 404
    assert session.committed is False


def test_cancel_task_rejects_terminal_task() -> None:
    user = SimpleNamespace(id=uuid4())
    task = make_task(user.id)
    task.status = "succeeded"
    session = FakeSession(
        results=[FakeResult(scalar=task)],
        expected_filters=[("tasks.user_id", user.id)],
    )
    client = make_client(session, user)

    response = client.post(f"/api/tasks/{task.id}/cancel")

    assert response.status_code == 409
    assert task.status == "succeeded"

"""Cross-session fencing, replay prevention and dispatch on real PostgreSQL."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.runtime import AgentRuntime
from app.api.routes.tasks import cancel_task, create_agent_task, update_task
from app.api.routes.task_schedules import run_task_schedule_now
from app.models import Conversation, Message, User
from app.models.task import Task
from app.models.task_schedule import TaskSchedule
from app.models.tool import ToolCall
from app.schemas import AgentTaskCreate, TaskUpdate
from app.services.agent_task_runner import AgentTaskRunner
from app.services.plugin_registry import PluginManifest, RegisteredTool
from app.services.task_lease import TaskLease, TaskLeaseLost, current_task_lease
from app.services.task_scheduler import TaskScheduler, recover_stale_running_tasks
from app.services.task_schedules import dispatch_due_schedules
from app.services.tool_executor import ToolExecutor

pytestmark = pytest.mark.integration
REQUEST = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


@pytest.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, conversation_id = uuid4(), uuid4()
    async with sessions() as session:
        name = f"task_{user_id.hex[:24]}"
        session.add(User(id=user_id, username=name, username_normalized=name, password_hash="synthetic-test"))
        await session.flush()
        session.add(Conversation(id=conversation_id, user_id=user_id))
        await session.commit()
    try:
        yield sessions, user_id, conversation_id
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


async def make_task(db, **overrides):
    sessions, user_id, conversation_id = db
    values = dict(user_id=user_id, conversation_id=conversation_id, name="Durable fixture",
                  kind="agent", status="queued", input_={"prompt": "Fixture"}, max_attempts=3,
                  deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    async with sessions() as session:
        task = Task(**(values | overrides))
        session.add(task)
        await session.commit()
        return task


def runtime_fixture():
    class Runtime:
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def stream(self, request):
            type(self).calls += 1
            self.started.set()
            await self.release.wait()
            yield {"event": "token", "data": json.dumps({"text": "Completed fixture"})}
            yield {"event": "done", "data": json.dumps({"conversation_id": str(request.conversation_id)})}
    return Runtime


@pytest.mark.asyncio
async def test_two_workers_claim_exactly_one_execution(database):
    sessions, _, _ = database
    task = await make_task(database)
    runtime = runtime_fixture()
    first = asyncio.create_task(AgentTaskRunner(sessions, None, runtime).run(task.id))
    await asyncio.wait_for(runtime.started.wait(), 3)
    await AgentTaskRunner(sessions, None, runtime).run(task.id)
    runtime.release.set()
    await first
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        assert saved.status == "succeeded" and saved.attempt_count == 1
        assert saved.run_token is None
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_recovered_task_rejects_late_old_worker_completion(database):
    sessions, _, _ = database
    task = await make_task(database)
    runtime = runtime_fixture()
    job = asyncio.create_task(AgentTaskRunner(sessions, None, runtime).run(task.id))
    await asyncio.wait_for(runtime.started.wait(), 3)
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        saved.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await recover_stale_running_tasks(session) == 1
    replacement = runtime_fixture()
    replacement.release.set()
    await AgentTaskRunner(sessions, None, replacement).run(task.id)
    runtime.release.set()
    await job
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        assert saved.status == "succeeded" and saved.attempt_count == 2
        assert len(saved.metadata_["events"]) == 1


@pytest.mark.asyncio
async def test_remote_cancel_stops_worker_on_heartbeat(database):
    sessions, user_id, _ = database
    task = await make_task(database)
    runtime = runtime_fixture()
    job = asyncio.create_task(AgentTaskRunner(sessions, None, runtime, heartbeat_seconds=1).run(task.id))
    await asyncio.wait_for(runtime.started.wait(), 3)
    async with sessions() as session:
        await cancel_task(task.id, REQUEST, SimpleNamespace(id=user_id), session)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, 3)
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        assert saved.status == "cancelled" and saved.run_token is None and saved.result is None


@pytest.mark.asyncio
async def test_attempt_timeout_and_total_deadline_bound_retry(database):
    sessions, _, _ = database
    task = await make_task(database, timeout_seconds=1, deadline_at=datetime.now(UTC) + timedelta(seconds=2))
    runtime = runtime_fixture()
    await asyncio.wait_for(AgentTaskRunner(sessions, None, runtime, retry_base_seconds=10).run(task.id), 4)
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        assert saved.status == "failed" and "deadline" in saved.error
        assert saved.attempt_count == 1


@pytest.mark.asyncio
async def test_expired_queued_task_never_runs_and_shutdown_preserves_attempt_budget(database):
    sessions, _, _ = database
    task = await make_task(database, deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    runtime = runtime_fixture()
    await AgentTaskRunner(sessions, None, runtime).run(task.id)
    assert runtime.calls == 0
    task2 = await make_task(database)
    scheduler = TaskScheduler(session_factory=sessions, runner=AgentTaskRunner(sessions, None, runtime))
    scheduler.enqueue(task2.id)
    await asyncio.wait_for(runtime.started.wait(), 3)
    await scheduler.shutdown()
    async with sessions() as session:
        saved = await session.get(Task, task2.id)
        assert saved.status == "queued" and saved.attempt_count == 1
        assert saved.run_token is None and saved.next_attempt_at is not None


def tool_fixture(calls):
    def handler():
        calls.append("dispatch")
        return "external fixture result"
    return RegisteredTool(PluginManifest(name="fixture", description="fixture", entrypoint="fixture:run"), handler, Path("."))


@pytest.mark.asyncio
async def test_tool_boundary_blocks_old_token_and_persists_replay_barrier(database):
    sessions, user_id, conversation_id = database
    now, token = datetime.now(UTC), uuid4()
    task = await make_task(database, status="running", run_token=token, attempt_count=1,
                           lease_expires_at=now + timedelta(seconds=60))
    calls, tool = [], None
    tool = tool_fixture(calls)
    context = current_task_lease.set(TaskLease(task.id, uuid4()))
    try:
        async with sessions() as session:
            with pytest.raises(TaskLeaseLost):
                await ToolExecutor().run(tool, {}, session=session, task_id=task.id, user_id=user_id)
        assert calls == []
        current_task_lease.set(TaskLease(task.id, token))
        async with sessions() as session:
            result = await ToolExecutor().run(tool, {}, session=session, task_id=task.id,
                                              user_id=user_id, conversation_id=conversation_id)
            assert result.status == "success"
        async with sessions() as session:
            saved = await session.get(Task, task.id)
            assert saved.replay_blocked_reason == "Tool dispatched: fixture"
            saved.lease_expires_at = now - timedelta(seconds=1)
            await session.commit()
            await recover_stale_running_tasks(session)
            assert saved.status == "stale" and "Automatic retry is disabled" in saved.error
            assert len((await session.scalars(select(ToolCall).where(ToolCall.task_id == task.id))).all()) >= 1
        assert calls == ["dispatch"]
    finally:
        current_task_lease.reset(context)


@pytest.mark.asyncio
async def test_runtime_messages_fenced_and_user_prompt_deduplicated(database):
    sessions, user_id, conversation_id = database
    token = uuid4()
    task = await make_task(database, status="running", run_token=token,
                           lease_expires_at=datetime.now(UTC) + timedelta(minutes=1))
    context = current_task_lease.set(TaskLease(task.id, token))
    try:
        async with sessions() as session:
            runtime = AgentRuntime(session, None, user_id=user_id, task_id=task.id)
            await runtime._save_message(conversation_id, "user", "fixture prompt")
            await runtime._save_message(conversation_id, "user", "fixture prompt")
            assert await runtime._load_recent_history(conversation_id) == []
            await runtime._save_message(conversation_id, "assistant", "fixture answer")
        current_task_lease.set(TaskLease(task.id, uuid4()))
        async with sessions() as session:
            with pytest.raises(TaskLeaseLost):
                await AgentRuntime(session, None, user_id=user_id, task_id=task.id)._save_message(conversation_id, "assistant", "stale overwrite")
        async with sessions() as session:
            messages = (await session.scalars(select(Message).where(Message.conversation_id == conversation_id))).all()
            assert len(messages) == 2
            assert (await session.get(Task, task.id)).replay_blocked_reason == "Assistant answer saved"
    finally:
        current_task_lease.reset(context)


@pytest.mark.asyncio
async def test_failed_attempt_after_dispatch_is_never_retried(database, monkeypatch):
    sessions, _, _ = database
    task = await make_task(database)
    dispatches = []

    async def dispatch(executor, tool, arguments, **kwargs):
        async with sessions() as observer:
            persisted = await observer.get(Task, task.id)
            assert persisted.replay_blocked_at is not None
        dispatches.append(True)
        return "effect completed"
    monkeypatch.setattr(ToolExecutor, "_dispatch_tool", dispatch)

    class FailedAfterTool:
        def __init__(self, **kwargs):
            self.session = kwargs["session"]

        async def stream(self, request):
            await ToolExecutor().run(tool_fixture([]), {}, session=self.session, task_id=task.id)
            raise TimeoutError("provider timed out after tool")
            yield

    outcome = await AgentTaskRunner(sessions, None, FailedAfterTool).run(task.id)
    assert outcome.retry_at is None
    async with sessions() as session:
        saved = await session.get(Task, task.id)
        assert saved.status == "failed" and saved.attempt_count == 1
        assert "Automatic retry is disabled" in saved.error
    assert len(dispatches) == 1


async def make_schedule(database, **overrides):
    sessions, user_id, conversation_id = database
    now = datetime.now(UTC)
    values = dict(user_id=user_id, conversation_id=conversation_id, name="Scheduled fixture", prompt="Fixture",
                  schedule_kind="interval", interval_seconds=60, timezone="UTC", next_run_at=now - timedelta(seconds=1))
    async with sessions() as session:
        schedule = TaskSchedule(**(values | overrides))
        session.add(schedule)
        await session.commit()
        return schedule


@pytest.mark.asyncio
async def test_dispatch_concurrency_overlap_and_manual_run_deduplication(database):
    sessions, user_id, _ = database
    schedule = await make_schedule(database)
    async def dispatch():
        async with sessions() as session:
            return await dispatch_due_schedules(session)
    results = await asyncio.gather(dispatch(), dispatch())
    assert sum(map(len, results)) == 1
    first_id = next(item for items in results for item in items)
    async with sessions() as session:
        repeated = await run_task_schedule_now(schedule.id, REQUEST, SimpleNamespace(id=user_id), session)
        assert repeated.id == first_id
    async with sessions() as session:
        saved = await session.get(TaskSchedule, schedule.id)
        saved.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await dispatch_due_schedules(session) == []
        await session.refresh(saved)
        assert saved.last_dispatch_status == "overlap_skipped"


@pytest.mark.asyncio
async def test_missed_and_invalid_schedule_do_not_block_valid_dispatch(database):
    sessions, _, _ = database
    missed = await make_schedule(database, schedule_kind="once", next_run_at=datetime.now(UTC) - timedelta(hours=1))
    invalid = await make_schedule(database, interval_seconds=1)
    await make_schedule(database)
    async with sessions() as session:
        assert len(await dispatch_due_schedules(session)) == 1
        saved = await session.get(TaskSchedule, missed.id)
        assert saved.enabled is False and saved.last_dispatch_status == "missed"
        saved = await session.get(TaskSchedule, invalid.id)
        assert saved.enabled is False and saved.last_dispatch_status == "invalid"


@pytest.mark.asyncio
async def test_api_request_key_conflicts_and_worker_owned_state(database):
    sessions, user_id, _ = database
    user = SimpleNamespace(id=user_id)
    payload = AgentTaskCreate(prompt="Fixture", idempotency_key=uuid4().hex)
    async with sessions() as session:
        first = await create_agent_task(payload, REQUEST, user, session)
    # A worker attaches its conversation after creation; original request identity survives.
    runtime = runtime_fixture()
    runtime.release.set()
    await AgentTaskRunner(sessions, None, runtime).run(first.id)
    async with sessions() as session:
        repeated = await create_agent_task(payload, REQUEST, user, session)
        assert repeated.id == first.id
        with pytest.raises(HTTPException) as conflict:
            await create_agent_task(payload.model_copy(update={"prompt": "Different"}), REQUEST, user, session)
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as managed:
            await update_task(first.id, TaskUpdate(status="running"), user, session)
        assert managed.value.status_code == 409

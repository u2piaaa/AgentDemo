import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.runtime import AgentRuntime
from app.models.task import TASK_KIND_AGENT
from app.schemas import AgentExecutionState, AgentToolPlan, ToolRunResponse
from app.services.agent_task_runner import AgentTaskRunner


class FakeSession:
    def __init__(self, task) -> None:
        self.task = task
        self.commits = 0
        self.last_get_kwargs = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def get(self, model, task_id, **kwargs):
        self.last_get_kwargs = kwargs
        return self.task if self.task.id == task_id else None

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, task) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        self.calls = getattr(self, "calls", 0) + 1
        return self.session


class SuccessfulRuntime:
    init_kwargs = None
    request = None

    def __init__(self, **kwargs) -> None:
        type(self).init_kwargs = kwargs

    async def stream(self, request):
        type(self).request = request
        yield event("status", {"label": "planning", "trace_id": "trace-agent"})
        yield event("token", {"text": "Completed in the background."})
        yield event(
            "done",
            {
                "conversation_id": str(request.conversation_id),
                "trace_id": "trace-agent",
                "citations": [],
                "tool_calls": [],
                "model_route": {"model_name": "fake"},
            },
        )


class ConfirmationRuntime:
    def __init__(self, **kwargs) -> None:
        pass

    async def stream(self, request):
        yield event(
            "tool_result",
            {
                "tool_name": "read_file",
                "status": "failed",
                "error": "Tool requires confirmation before execution",
                "trace_id": "trace-confirm",
            },
        )
        yield event(
            "done",
            {
                "conversation_id": str(request.conversation_id),
                "trace_id": "trace-confirm",
                "citations": [],
                "tool_calls": [],
                "model_route": {"model_name": "fake"},
            },
        )


def event(event_type: str, data: dict) -> dict[str, str]:
    return {"event": event_type, "data": json.dumps(data)}


def make_task():
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        name="Background task",
        kind=TASK_KIND_AGENT,
        input_={"prompt": "Research the topic"},
        status="queued",
        progress=0,
        error=None,
        result=None,
        trace_id=None,
        metadata_={},
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
        attempt_count=0,
        max_attempts=3,
        next_attempt_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
    )


@pytest.mark.asyncio
async def test_agent_task_runner_persists_success_and_runtime_identity() -> None:
    task = make_task()
    session = FakeSession(task)
    registry = object()
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        registry,
        runtime_factory=SuccessfulRuntime,
    )

    await runner.run(task.id)

    assert task.status == "succeeded"
    assert task.progress == 100
    assert task.trace_id == "trace-agent"
    assert task.result["answer"] == "Completed in the background."
    assert task.started_at is not None
    assert task.finished_at is not None
    assert task.attempt_count == 1
    assert task.lease_expires_at is None
    assert [item["type"] for item in task.metadata_["events"]] == ["status", "done"]
    assert SuccessfulRuntime.init_kwargs["task_id"] == task.id
    assert SuccessfulRuntime.init_kwargs["user_id"] == task.user_id
    assert SuccessfulRuntime.init_kwargs["plugin_registry"] is registry
    assert SuccessfulRuntime.request.task_type == "background"
    assert SuccessfulRuntime.request.message == "Research the topic"
    assert runner.session_factory.calls == 2
    assert session.last_get_kwargs == {"with_for_update": {"skip_locked": True}}


@pytest.mark.asyncio
async def test_agent_task_runner_fails_safely_when_tool_confirmation_is_needed() -> None:
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=ConfirmationRuntime,
    )

    await runner.run(task.id)

    assert task.status == "failed"
    assert "interactive chat" in task.error
    assert task.finished_at is not None


class TransientFailureRuntime:
    def __init__(self, **kwargs) -> None:
        pass

    async def stream(self, request):
        raise TimeoutError("upstream timed out")
        yield


class AuthenticationFailureRuntime:
    def __init__(self, **kwargs) -> None:
        pass

    async def stream(self, request):
        raise RuntimeError("invalid API key")
        yield


@pytest.mark.asyncio
async def test_agent_task_runner_retries_transient_failure_with_backoff() -> None:
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=TransientFailureRuntime,
        retry_base_seconds=1,
    )

    outcome = await runner.run(task.id)

    assert task.status == "queued"
    assert task.attempt_count == 1
    assert task.next_attempt_at is not None
    assert outcome.retry_at == task.next_attempt_at
    assert "retry scheduled" in task.error
    assert task.metadata_["events"][-1]["type"] == "retry_scheduled"


@pytest.mark.asyncio
async def test_agent_task_runner_does_not_retry_authentication_failure() -> None:
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=AuthenticationFailureRuntime,
    )

    outcome = await runner.run(task.id)

    assert outcome.retry_at is None
    assert task.status == "failed"
    assert task.attempt_count == 1
    assert task.next_attempt_at is None


class UnknownFailureRuntime:
    def __init__(self, **kwargs) -> None:
        pass

    async def stream(self, request):
        raise RuntimeError("provider stream closed")
        yield


@pytest.mark.asyncio
async def test_agent_task_runner_retries_unknown_provider_failure() -> None:
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=UnknownFailureRuntime,
        retry_base_seconds=1,
    )

    outcome = await runner.run(task.id)

    assert outcome.retry_at is not None
    assert task.status == "queued"


class WaitingRuntime:
    started = asyncio.Event()

    def __init__(self, **kwargs) -> None:
        pass

    async def stream(self, request):
        type(self).started.set()
        await asyncio.Event().wait()
        if False:
            yield {}


@pytest.mark.asyncio
async def test_agent_task_runner_marks_cancelled_when_worker_is_cancelled() -> None:
    WaitingRuntime.started = asyncio.Event()
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=WaitingRuntime,
    )
    job = asyncio.create_task(runner.run(task.id))
    await WaitingRuntime.started.wait()

    # The API writes the terminal status before cancelling its in-process worker.
    task.status = "cancelled"
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_agent_task_runner_requeues_worker_interrupted_by_shutdown() -> None:
    WaitingRuntime.started = asyncio.Event()
    task = make_task()
    session = FakeSession(task)
    runner = AgentTaskRunner(
        FakeSessionFactory(session),  # type: ignore[arg-type]
        object(),
        runtime_factory=WaitingRuntime,
    )
    job = asyncio.create_task(runner.run(task.id))
    await WaitingRuntime.started.wait()

    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert task.status == "queued"
    assert task.attempt_count == 0
    assert task.next_attempt_at is not None
    assert task.lease_expires_at is None


class CaptureExecutor:
    def __init__(self) -> None:
        self.kwargs = None

    async def run(self, tool, arguments, **kwargs):
        self.kwargs = kwargs
        return ToolRunResponse(tool_name=tool.manifest.name)


@pytest.mark.asyncio
async def test_agent_runtime_binds_tool_calls_to_background_task() -> None:
    task_id = uuid4()
    tool = SimpleNamespace(manifest=SimpleNamespace(name="example"))
    registry = SimpleNamespace(get=lambda name: tool)
    executor = CaptureExecutor()
    runtime = AgentRuntime(
        session=SimpleNamespace(),
        plugin_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        task_id=task_id,
    )
    state = AgentExecutionState(
        task_id=task_id,
        message="Run the tool",
        plan=AgentToolPlan(no_tool=False, tool_name="example", arguments={"value": 1}),
    )

    await runtime._execute_tool_plan(state)

    assert executor.kwargs["task_id"] == task_id

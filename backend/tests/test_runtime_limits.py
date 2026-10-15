import asyncio
import json
from unittest.mock import AsyncMock

import anyio
import pytest

from app.agent.runtime import AgentRuntime
from app.models.conversation import Message
from app.schemas import AgentExecutionState, ChatRequest, ToolConfirmationRequest
from test_agent_runtime import (
    FakeGateway, FakeRag, FakeRegistry, FakeSession, assistant_messages,
    collect_events, make_read_file_tool, successful_read_file,
)


class WaitingGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream_reply(self, **kwargs):
        self.entered.set()
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            self.closed.set()


def runtime_with(gateway=None, rag=None):
    return AgentRuntime(
        session=FakeSession(), plugin_registry=None, model_gateway=gateway or FakeGateway(),
        rag_service=rag or FakeRag(),
    )


async def test_retrieval_timeout_falls_back_to_chat_and_reports_status():
    rag = FakeRag()
    stopped = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    rag.search = wait_forever
    runtime = runtime_with(rag=rag)
    runtime.settings = runtime.settings.model_copy(update={"agent_retrieval_timeout_seconds": 0.01})
    runtime.session.rollback = AsyncMock()
    # Isolate retrieval recovery from memory compaction's separate transaction.
    runtime._maybe_update_memory_summary = AsyncMock()
    events = await asyncio.wait_for(collect_events(runtime, "hello"), timeout=2)
    assert stopped.is_set()
    assert any(name == "status" and data["label"] == "retrieval_timeout" for name, data in events)
    assert events[-1][0] == "done"
    runtime.session.rollback.assert_awaited_once()


async def test_graph_deadline_terminates_waiting_provider_and_does_not_save_partial_answer():
    gateway = WaitingGateway()
    runtime = runtime_with(gateway)
    # Allow graph setup under coverage/loaded CI before testing provider cancellation.
    runtime.settings = runtime.settings.model_copy(update={"agent_run_timeout_seconds": 0.5})
    events = await asyncio.wait_for(collect_events(runtime, "hello"), timeout=2)
    assert gateway.closed.is_set()
    assert events[-1][0] == "error"
    assert "total time budget" in events[-1][1]["message"]
    assert not assistant_messages(runtime.session)
    assert not any(name in {"done", "token"} for name, _ in events)


async def test_client_cancellation_propagates_and_closes_provider():
    gateway = WaitingGateway()
    runtime = runtime_with(gateway)
    task = asyncio.create_task(collect_events(runtime, "hello"))
    await asyncio.wait_for(gateway.entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gateway.closed.is_set()
    assert not assistant_messages(runtime.session)


async def blocked_runtime():
    runtime = runtime_with()
    runtime.plugin_registry = FakeRegistry(make_read_file_tool(successful_read_file, requires_confirmation=True))
    await collect_events(runtime, "Read README.md")
    approval = assistant_messages(runtime.session)[0]
    return runtime, approval, ToolConfirmationRequest(
        conversation_id=approval.conversation_id, approval_id=approval.id,
        message="Read README.md", tool_name="read_file", arguments={"path": "README.md"},
    )


async def test_confirmed_continuation_uses_saved_context_without_retrieval_or_tool_replay():
    runtime, approval, request = await blocked_runtime()
    approval.metadata_["continuation"]["observations"] = ["Earlier tool result from persisted approval"]
    runtime.rag.search = AsyncMock(side_effect=AssertionError("must not retrieve again"))
    events = [event async for event in runtime.stream_confirmed_tool(request)]
    assert events[-1]["event"] == "done"
    assert approval.metadata_["approval"]["status"] == "completed"
    assert approval.metadata_["tool_calls"][-1]["approval_status"] == "completed"
    assert "Earlier tool result from persisted approval" in "\n".join(runtime.model_gateway.stream_calls[-1]["context"])
    assert sum(event["event"] == "tool_result" for event in events) == 1
    runtime.rag.search.assert_not_awaited()
    saved = assistant_messages(runtime.session)[-1]
    assert saved.metadata_["resolves_approval_id"] == str(approval.id)


async def test_confirmed_stream_cancellation_durably_marks_interruption():
    runtime, approval, request = await blocked_runtime()
    gateway = WaitingGateway()
    runtime.model_gateway = gateway

    async def consume():
        return [event async for event in runtime.stream_confirmed_tool(request)]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(gateway.entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gateway.closed.is_set()
    assert approval.metadata_["approval"]["status"] == "interrupted"
    assert len(assistant_messages(runtime.session)) == 1


async def test_closing_confirmed_generator_explicitly_finalizes_approval():
    runtime, approval, request = await blocked_runtime()
    stream = runtime.stream_confirmed_tool(request)
    await anext(stream)
    await stream.aclose()
    assert approval.metadata_["approval"]["status"] == "interrupted"


async def test_sse_level_cancellation_allows_bounded_approval_cleanup():
    runtime, approval, request = await blocked_runtime()
    runtime.model_gateway = WaitingGateway()
    cleanup_finished = asyncio.Event()
    original_interrupt = runtime.approvals.interrupt

    async def interrupt(message):
        await anyio.sleep(0)
        await original_interrupt(message)
        cleanup_finished.set()

    runtime.approvals.interrupt = interrupt
    with anyio.move_on_after(0.05):
        async for _ in runtime.stream_confirmed_tool(request):
            pass
    assert cleanup_finished.is_set()
    assert approval.metadata_["approval"]["status"] == "interrupted"


async def test_oversized_request_is_rejected_before_any_graph_side_effect():
    runtime = runtime_with()
    runtime.settings = runtime.settings.model_copy(update={"agent_context_max_chars": 1000})
    events = await collect_events(runtime, "x" * 1001)
    assert [name for name, _ in events] == ["error"]
    assert runtime.session.items == []


async def test_confirmed_run_deadline_returns_error_and_consumes_approval():
    runtime, approval, request = await blocked_runtime()
    runtime.model_gateway = WaitingGateway()
    runtime.settings = runtime.settings.model_copy(update={"agent_run_timeout_seconds": 0.05})
    events = [event async for event in runtime.stream_confirmed_tool(request)]
    assert events[-1]["event"] == "error"
    assert "total time budget" in json.loads(events[-1]["data"])["message"]
    assert approval.metadata_["approval"]["status"] == "interrupted"


async def test_answer_context_budget_is_applied_before_gateway():
    runtime = runtime_with()
    runtime.settings = runtime.settings.model_copy(update={"agent_context_max_chars": 1000})
    state = AgentExecutionState(
        message="current request", trace_id="budget-test",
        history=[{"role": "user", "content": "old context" * 1000}],
        memory_summaries=["Old summary " * 1000], observations=["Latest tool result " * 1000],
    )
    async for _ in runtime._generate_answer(state, "fixture"):
        pass
    call = runtime.model_gateway.stream_calls[-1]
    assert call["prompt"] == "current request"
    assert call["history"] == []
    assert sum(map(len, call["context"])) + len(call["prompt"]) <= 1000
    assert "Latest tool result" in "\n".join(call["context"])
    assert state.context_budget["input_chars"] <= 1000
    assert state.context_budget["dropped_history_messages"] == 1


async def test_empty_approval_messages_do_not_trigger_memory_summary():
    runtime = runtime_with()
    runtime.settings = runtime.settings.model_copy(update={"agent_memory_message_limit": 1})
    runtime.session.history = [Message(role="assistant", content="") for _ in range(10)]
    await runtime._maybe_update_memory_summary(ChatRequest(message="hello").conversation_id)
    assert runtime.model_gateway.summary_calls == []

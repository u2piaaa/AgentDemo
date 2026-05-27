import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.runtime import AgentRuntime
from app.models.conversation import Conversation, Message
from app.schemas import AgentToolPlan, ChatRequest
from app.services.model_gateway import ModelRoute
from app.services.plugin_registry import PluginManifest, RegisteredTool


class ScalarResult:
    def __init__(self, items: list) -> None:
        self.items = items

    def all(self) -> list:
        return self.items

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self.items[0] if self.items else None


class FakeSession:
    def __init__(self, history: list[Message] | None = None) -> None:
        self.items = []
        self.history = history or []
        self.commit_count = 0

    def add(self, item) -> None:
        self.items.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commit_count += 1

    async def execute(self, statement):
        return ScalarResult(self.history)


class FakeRag:
    async def search(self, query: str, conversation_id=None):
        return []


class FakeGateway:
    def __init__(self) -> None:
        self.stream_calls = []

    def route(self, task_type: str, prompt: str) -> ModelRoute:
        return ModelRoute(model_name="fake-chat", provider="fake", reason=f"fake_{task_type}")

    async def stream_reply(
        self,
        model_name: str,
        prompt: str,
        context: list[str],
        history: list[dict[str, str]] | None = None,
    ):
        self.stream_calls.append(
            {
                "model_name": model_name,
                "prompt": prompt,
                "context": context,
                "history": history or [],
            }
        )
        joined_context = "\n".join(context)
        if "failed with status" in joined_context:
            text = "I could not read the file, so I cannot summarize it."
        elif "AgentDemo" in joined_context:
            text = "README says AgentDemo is the project."
        else:
            text = "plain chat response"
        for token in text.split(" "):
            yield token + " "


class FakeRegistry:
    def __init__(self, tool: RegisteredTool | None) -> None:
        self.tool = tool

    def get(self, name: str) -> RegisteredTool | None:
        if name == "read_file":
            return self.tool
        return None


def make_read_file_tool(handler) -> RegisteredTool:
    manifest = PluginManifest(
        name="read_file",
        description="Read a file.",
        permission="filesystem_read",
        requires_confirmation=False,
        parameters={
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string", "format": "path"}},
        },
        entrypoint="tool.py:run",
    )
    return RegisteredTool(manifest=manifest, handler=handler, base_dir=Path("."))


def successful_read_file(path: str) -> dict[str, str | int]:
    return {"path": path, "chars": 24, "content": "AgentDemo runtime README"}


def failing_read_file(path: str) -> None:
    raise FileNotFoundError(f"missing {path}")


async def collect_events(runtime: AgentRuntime, message: str) -> list[tuple[str, dict]]:
    events = []
    async for event in runtime.stream(ChatRequest(message=message)):
        events.append((event["event"], json.loads(event["data"])))
    return events


def assistant_messages(session: FakeSession) -> list[Message]:
    return [item for item in session.items if isinstance(item, Message) and item.role == "assistant"]


@pytest.mark.asyncio
async def test_plain_chat_uses_fake_gateway_without_tool() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "hello there")

    assert "tool_call" not in [name for name, _ in events]
    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert assistant_messages(session)[0].content == "plain chat response "
    assert gateway.stream_calls[0]["context"] == []


@pytest.mark.asyncio
async def test_read_file_request_triggers_tool_and_influences_answer() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "请读取 README.md 并总结")

    names = [name for name, _ in events]
    assert "tool_call" in names
    assert "tool_result" in names
    assert next(data for name, data in events if name == "tool_call")["arguments"] == {
        "path": "README.md"
    }
    assert next(data for name, data in events if name == "tool_result")["status"] == "success"
    assert "AgentDemo" in "\n".join(gateway.stream_calls[0]["context"])
    assert assistant_messages(session)[0].content == "README says AgentDemo is the project. "


@pytest.mark.asyncio
async def test_tool_failure_is_available_to_final_answer() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(failing_read_file)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "读取 README.md 并总结")

    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_result["status"] == "failed"
    assert "could not read" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_sse_event_order_for_tool_call() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    names = [name for name, _ in await collect_events(runtime, "读取 README.md 并总结")]

    assert names.index("plan") < names.index("tool_call")
    assert names.index("tool_call") < names.index("tool_result")
    assert names.index("tool_result") < names.index("token")
    assert names[-1] == "done"


@pytest.mark.asyncio
async def test_assistant_metadata_persists_trace_tool_calls_and_route() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "读取 README.md 并总结")

    metadata = assistant_messages(session)[0].metadata_
    done = events[-1][1]
    assert metadata["trace_id"] == done["trace_id"]
    assert metadata["tool_calls"][0]["tool_name"] == "read_file"
    assert metadata["model_route"] == {
        "model_name": "fake-chat",
        "provider": "fake",
        "reason": "fake_conversation",
    }


class LoopingRuntime(AgentRuntime):
    def _plan_next_step(self, state) -> AgentToolPlan:
        return AgentToolPlan(
            no_tool=False,
            tool_name="read_file",
            arguments={"path": "README.md"},
            reason="loop",
            requires_confirmation=False,
        )


@pytest.mark.asyncio
async def test_max_tool_rounds_stops_looping_planner() -> None:
    runtime = LoopingRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
        max_tool_rounds=2,
    )

    events = await collect_events(runtime, "读取 README.md 并总结")

    assert [name for name, _ in events].count("tool_call") == 2
    assert [data for name, data in events if name == "plan"][-1]["reason"].startswith(
        "Stopped after the maximum"
    )


def test_chinese_title_normalization_strips_common_prefixes() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=None,
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    assert runtime._summarize_title("请帮我：总结 README.md。") == "总结 README.md"
    assert runtime._summarize_title("能否读取 README.md 并总结？") == "读取 README.md 并总结"
    assert runtime._summarize_title("可以：记住项目代号 Aurora。") == "记住项目代号 Aurora"

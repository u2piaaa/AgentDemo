import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.runtime import AgentRuntime
from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.models.conversation import Conversation, MemorySummary, Message
from app.schemas import AgentToolPlan, ChatRequest, ToolConfirmationRequest
from app.services.model_gateway import ModelRoute, StructuredToolPlan
from app.services.plugin_registry import PluginManifest, PluginRegistry, RegisteredTool


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
        for item in self.items:
            if isinstance(item, Conversation) and item.id is None:
                item.id = uuid4()

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
        self.summary_calls = []

    def route(self, task_type: str, prompt: str) -> ModelRoute:
        return ModelRoute(model_name="fake-chat", provider="fake", reason=f"fake_{task_type}")

    def plan_tool_call(self, prompt: str, candidates: list[dict]) -> StructuredToolPlan:
        lowered = prompt.lower()
        for tool in candidates:
            names = [
                str(tool.get("name") or "").lower(),
                str(tool.get("provider_tool_id") or "").lower(),
            ]
            if any(name and name in lowered for name in names):
                return StructuredToolPlan(
                    no_tool=False,
                    tool_name=str(tool["name"]),
                    arguments={},
                    reason="fake mcp plan",
                )
        return StructuredToolPlan(no_tool=True, reason="fake no tool")

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
        elif "Web search results" in joined_context:
            text = "Search says the current result is available at https://example.com/news."
        elif "AgentDemo" in joined_context:
            text = "README says AgentDemo is the project."
        else:
            text = "plain chat response"
        for token in text.split(" "):
            yield token + " "

    async def summarize_messages(self, messages, existing_summary=None):
        self.summary_calls.append(
            {"messages": messages, "existing_summary": existing_summary}
        )
        return "The user prefers concise project notes."


class FakeRegistry:
    def __init__(self, tool: RegisteredTool | list[RegisteredTool] | None) -> None:
        if isinstance(tool, list):
            self.tools = {item.manifest.name: item for item in tool}
        elif tool is None:
            self.tools = {}
        else:
            self.tools = {tool.manifest.name: tool}

    def get(self, name: str) -> RegisteredTool | None:
        return self.tools.get(name)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self.tools.values())


def make_read_file_tool(handler, requires_confirmation: bool = False) -> RegisteredTool:
    manifest = PluginManifest(
        name="read_file",
        description="Read a file.",
        permission="filesystem_read",
        requires_confirmation=requires_confirmation,
        parameters={
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string", "format": "path"}},
        },
        entrypoint="tool.py:run",
    )
    return RegisteredTool(manifest=manifest, handler=handler, base_dir=Path("."))


def make_web_search_tool(handler, requires_confirmation: bool = False) -> RegisteredTool:
    manifest = PluginManifest(
        name="web_search",
        description="Search the web.",
        permission="network",
        requires_confirmation=requires_confirmation,
        parameters={
            "type": "object",
            "required": ["query"],
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "recency_days": {"type": "integer"},
            },
        },
        entrypoint="tool.py:run",
    )
    return RegisteredTool(manifest=manifest, handler=handler, base_dir=Path("."))


def successful_read_file(path: str) -> dict[str, str | int]:
    return {"path": path, "chars": 24, "content": "AgentDemo runtime README"}


def failing_read_file(path: str) -> None:
    raise FileNotFoundError(f"missing {path}")


def successful_web_search(query: str, max_results: int | None = None, recency_days: int | None = None):
    return {
        "query": query,
        "provider": "mock",
        "results": [
            {
                "title": "Current AI news",
                "url": "https://example.com/news",
                "snippet": "A current search result.",
                "source": "mock",
                "published_at": None,
            }
        ],
        "recency_days": recency_days,
    }


async def make_mcp_registry() -> UnifiedToolRegistry:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fake",
                    tools=[
                        {
                            "name": "lookup",
                            "description": "Lookup from fake MCP.",
                            "inputSchema": {"type": "object"},
                            "annotations": {"permission": "read"},
                            "mock_result": {
                                "content": [{"type": "text", "text": "fake mcp value"}]
                            },
                        }
                    ],
                    resources=[
                        {
                            "uri": "mcp://fake/doc",
                            "name": "fake doc",
                            "text": "resource context from MCP",
                        }
                    ],
                    prompts=[
                        {
                            "name": "tool_planning",
                            "messages": [{"role": "user", "content": "prompt from MCP"}],
                        }
                    ],
                )
            ]
        )
    )
    registry = UnifiedToolRegistry(PluginRegistry(Path(".")), client)
    await registry.refresh_mcp_tools()
    return registry


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
async def test_web_search_request_triggers_tool_and_influences_answer() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "联网搜索今天的 AI 新闻")

    names = [name for name, _ in events]
    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert "tool_call" in names
    assert tool_call["tool_name"] == "web_search"
    assert tool_call["arguments"]["query"] == "联网搜索今天的 AI 新闻"
    assert tool_call["arguments"]["recency_days"] == 1
    assert tool_result["status"] == "success"
    assert "Web search results" in "\n".join(gateway.stream_calls[0]["context"])
    assert "https://example.com/news" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_web_search_call_is_persisted_in_assistant_metadata() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "latest AI news")

    metadata = assistant_messages(session)[0].metadata_
    done = events[-1][1]
    assert metadata["tool_calls"][0]["tool_name"] == "web_search"
    assert metadata["tool_calls"][0]["requires_confirmation"] is False
    assert done["tool_calls"][0]["tool_name"] == "web_search"


@pytest.mark.asyncio
async def test_fake_mcp_tool_is_planned_and_called() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "Use fake lookup for this answer")

    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_call["provider"] == "mcp_server"
    assert tool_call["server_name"] == "fake"
    assert tool_result["provider"] == "mcp_server"
    assert tool_result["output"]["content"] == "fake mcp value"
    metadata = assistant_messages(session)[0].metadata_
    assert metadata["tool_calls"][0]["provider"] == "mcp_server"


@pytest.mark.asyncio
async def test_mcp_resource_and_prompt_enter_answer_context() -> None:
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=await make_mcp_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "Use mcp://fake/doc and tool_planning as context.",
    )

    context = "\n".join(gateway.stream_calls[0]["context"])
    assert "resource context from MCP" in context
    assert "prompt from MCP" in context
    done = events[-1][1]
    assert done["mcp_resources"][0]["uri"] == "mcp://fake/doc"


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


@pytest.mark.asyncio
async def test_confirmed_tool_stream_executes_blocked_tool() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(
            make_read_file_tool(successful_read_file, requires_confirmation=True)
        ),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    blocked_events = await collect_events(runtime, "读取 README.md 并总结")
    blocked_result = next(data for name, data in blocked_events if name == "tool_result")
    assert blocked_result["status"] == "failed"
    assert blocked_result["error"] == "Tool requires confirmation before execution"

    conversation_id = next(item.id for item in session.items if isinstance(item, Conversation))
    confirmed_events = []
    async for event in runtime.stream_confirmed_tool(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message="读取 README.md 并总结",
            tool_name="read_file",
            arguments={"path": "README.md"},
            reason="User confirmed the file read.",
        )
    ):
        confirmed_events.append((event["event"], json.loads(event["data"])))

    confirmed_result = next(data for name, data in confirmed_events if name == "tool_result")
    assert confirmed_result["status"] == "success"
    assert "AgentDemo" in "\n".join(gateway.stream_calls[-1]["context"])


class MemoryContextRuntime(AgentRuntime):
    async def _load_active_memory_summaries(self, conversation_id):
        return ["The user prefers terse implementation notes."]

    async def _maybe_update_memory_summary(self, conversation_id):
        return None


@pytest.mark.asyncio
async def test_runtime_loads_memory_summaries_into_answer_context() -> None:
    gateway = FakeGateway()
    runtime = MemoryContextRuntime(
        session=FakeSession(),
        plugin_registry=None,
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "What should you remember?")

    assert gateway.stream_calls[0]["context"] == [
        "Memory summary:\nThe user prefers terse implementation notes."
    ]


class SummarySession(FakeSession):
    def __init__(self, messages: list[Message], summaries: list[MemorySummary] | None = None):
        super().__init__(history=[])
        self.messages = messages
        self.summaries = summaries or []

    async def execute(self, statement):
        text = str(statement)
        if "memory_summaries" in text:
            return ScalarResult(self.summaries)
        return ScalarResult(self.messages)


@pytest.mark.asyncio
async def test_runtime_generates_memory_summary_for_long_conversation() -> None:
    conversation_id = uuid4()
    messages = [
        Message(conversation_id=conversation_id, role="user", content=f"fact {index}")
        for index in range(4)
    ]
    session = SummarySession(messages)
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=None,
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )
    runtime.settings.agent_memory_message_limit = 3

    await runtime._maybe_update_memory_summary(conversation_id)

    summaries = [item for item in session.items if isinstance(item, MemorySummary)]
    assert summaries[0].summary == "The user prefers concise project notes."
    assert gateway.summary_calls[0]["messages"][0]["content"] == "fact 0"


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

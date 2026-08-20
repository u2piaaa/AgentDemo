import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.runtime import AgentRuntime
from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.models.conversation import Conversation, MemorySummary, Message
from app.models.tool import ToolCall
from app.schemas import AgentToolPlan, ChatRequest, ToolConfirmationRequest
from app.services.model_gateway import ModelRoute, StructuredToolPlan
from app.services.plugin_registry import (
    TOOL_PROVIDER_MCP_SERVER,
    PluginManifest,
    PluginRegistry,
    RegisteredTool,
)


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
    def __init__(
        self,
        normalized_query: str | None = "latest AI news",
        normalize_error: Exception | None = None,
        reply_text: str | None = None,
    ) -> None:
        self.stream_calls = []
        self.summary_calls = []
        self.normalized_query = normalized_query
        self.normalize_error = normalize_error
        self.normalize_calls = []
        self.reply_text = reply_text

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

    async def normalize_web_search_query(self, message: str) -> str:
        self.normalize_calls.append(message)
        if self.normalize_error is not None:
            raise self.normalize_error
        return self.normalized_query or ""

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
        if self.reply_text is not None:
            text = self.reply_text
        elif "All search result page fetches failed" in joined_context:
            text = "Search snippets are available, but page-body fetching failed. Source: https://example.com/news."
        elif "Fetched content:" in joined_context:
            text = "Fetched search page content says AI investment is rising. Source: https://example.com/news."
        elif "failed with status" in joined_context:
            text = "I could not read the file, so I cannot summarize it."
        elif "GitHub repository content" in joined_context:
            text = "GitHub MCP returned repository content from https://github.com/u2piaaa/AgentDemo."
        elif "Fetched web page content" in joined_context:
            text = "The page says Example Domain. Source: https://example.com."
        elif "Hugging Face Hub MCP results" in joined_context:
            text = "Hugging Face returned Qwen/Qwen3 at https://huggingface.co/Qwen/Qwen3."
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
    def __init__(
        self,
        tool: RegisteredTool | list[RegisteredTool] | None,
        mcp_client=None,
    ) -> None:
        if isinstance(tool, list):
            self.tools = {item.manifest.name: item for item in tool}
        elif tool is None:
            self.tools = {}
        else:
            self.tools = {tool.manifest.name: tool}
        self.mcp_client = mcp_client

    def get(self, name: str) -> RegisteredTool | None:
        return self.tools.get(name)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self.tools.values())


class EmptyNamedMcpClient:
    async def list_resources(self):
        return [{"server_name": "fetch", "uri": "", "name": ""}]

    async def read_resource(self, server_name: str, uri: str):
        raise AssertionError("empty MCP resource names must not be read")

    async def list_prompts(self):
        return [
            {"server_name": "fetch", "name": ""},
            {
                "server_name": "fetch",
                "name": "fetch",
                "arguments": [{"name": "url", "required": True}],
            },
        ]

    async def get_prompt(self, server_name: str, name: str):
        raise AssertionError("empty MCP prompt names must not be loaded")


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


class FakeFetchMcpClient:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[dict] = []

    async def call_tool(self, server_name: str, provider_tool_id: str, arguments: dict):
        self.calls.append(dict(arguments))
        url = str(arguments.get("url") or "")
        if url in self.failures:
            raise RuntimeError(f"fetch failed for {url}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"# Page for {url}\n\nFetched body content for {url}.",
                }
            ],
            "url": url,
        }


def make_mcp_fetch_tool(client: FakeFetchMcpClient | None = None) -> RegisteredTool:
    manifest = PluginManifest(
        name="mcp.fetch.fetch",
        description="Fetches a URL from the internet.",
        permission="network",
        requires_confirmation=True,
        parameters={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "max_length": {"type": "integer"},
            },
        },
        entrypoint="mcp:call_tool",
    )
    return RegisteredTool(
        manifest=manifest,
        handler=None,
        base_dir=Path("."),
        provider=TOOL_PROVIDER_MCP_SERVER,
        provider_tool_id="fetch",
        server_name="fetch",
        client=client or FakeFetchMcpClient(),
    )


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


def failing_web_search(query: str, max_results: int | None = None, recency_days: int | None = None):
    raise RuntimeError(
        "Web search provider is disabled. Set WEB_SEARCH_PROVIDER and WEB_SEARCH_API_KEY "
        "to enable live search."
    )


def web_search_with_results(results: list[dict]):
    def handler(query: str, max_results: int | None = None, recency_days: int | None = None):
        return {
            "query": query,
            "provider": "mock",
            "results": results,
            "recency_days": recency_days,
        }

    return handler


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


async def make_mcp_fetch_registry() -> UnifiedToolRegistry:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fetch",
                    tools=[
                        {
                            "name": "fetch",
                            "description": "Fetches a URL from the internet.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["url"],
                                "properties": {
                                    "url": {"type": "string"},
                                    "max_length": {"type": "integer"},
                                },
                            },
                            "mock_result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "# Example Domain\n\n"
                                            + ("This domain is for examples. " * 40)
                                            + "Late page detail after summary cutoff."
                                        ),
                                    }
                                ],
                                "url": "https://example.com",
                            },
                        }
                    ],
                )
            ]
        )
    )
    registry = UnifiedToolRegistry(PluginRegistry(Path(".")), client)
    await registry.refresh_mcp_tools()
    return registry


async def make_mcp_github_registry() -> UnifiedToolRegistry:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="github",
                    tools=[
                        {
                            "name": "get_file_contents",
                            "description": "Get file or directory contents from GitHub.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["owner", "repo"],
                                "properties": {
                                    "owner": {"type": "string"},
                                    "repo": {"type": "string"},
                                    "path": {"type": "string"},
                                    "ref": {"type": "string"},
                                },
                            },
                            "mock_result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "# AgentDemo\n\n"
                                            "backend/app/agent/runtime.py handles agent planning."
                                        ),
                                    }
                                ],
                            },
                        }
                    ],
                )
            ]
        )
    )
    registry = UnifiedToolRegistry(PluginRegistry(Path(".")), client)
    await registry.refresh_mcp_tools()
    return registry


async def make_mcp_huggingface_registry() -> UnifiedToolRegistry:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="huggingface",
                    transport="http",
                    url="https://huggingface.co/mcp",
                    tools=[
                        {
                            "name": "model_search",
                            "description": "Search Hugging Face models.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {"query": {"type": "string"}},
                            },
                            "mock_result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Qwen/Qwen3: https://huggingface.co/Qwen/Qwen3"
                                        ),
                                    }
                                ]
                            },
                        },
                        {
                            "name": "dataset_search",
                            "description": "Search Hugging Face datasets.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {"query": {"type": "string"}},
                            },
                            "mock_result": {
                                "content": [{"type": "text", "text": "weather dataset"}]
                            },
                        },
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
    assert not any("Available runtime tools" in item for item in gateway.stream_calls[0]["context"])


@pytest.mark.asyncio
async def test_model_tool_protocol_markup_is_not_streamed_or_saved() -> None:
    session = FakeSession()
    gateway = FakeGateway(
        reply_text=(
            "<｜｜DSML｜｜tool_calls>\n"
            "<invoke name=\"list_dir\"><parameter name=\"path\">.</parameter></invoke>"
        )
    )
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "用三句话介绍这个项目能做什么。")

    visible_text = "".join(data["text"] for name, data in events if name == "token")
    saved = assistant_messages(session)[0].content
    for text in (visible_text, saved):
        lowered = text.lower()
        assert "dsml" not in lowered
        assert "tool_calls" not in lowered
        assert "invoke name" not in lowered
    assert "AgentDemo" in saved
    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert "tool_call" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_model_tool_protocol_after_successful_read_uses_tool_result() -> None:
    session = FakeSession()
    gateway = FakeGateway(
        reply_text=(
            "<｜｜DSML｜｜tool_calls>\n"
            '<invoke name="read_file"><parameter name="path">README.md</parameter></invoke>'
        )
    )
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "请读取 README.md 并告诉我第一行")

    visible_text = "".join(data["text"] for name, data in events if name == "token")
    saved = assistant_messages(session)[0].content
    assert "AgentDemo runtime README" in visible_text
    assert saved == visible_text
    assert "请换一种方式重试" not in saved


@pytest.mark.asyncio
async def test_graph_plain_chat_matches_runtime_contract() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=None,
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "hello there")

    assert [name for name, _ in events[:6]] == [
        "status",
        "status",
        "status",
        "status",
        "status",
        "plan",
    ]
    assert [data["label"] for name, data in events if name == "status"][:5] == [
        "ensure_conversation",
        "load_history",
        "save_user_message",
        "retrieving_context",
        "planning",
    ]
    assert "tool_call" not in [name for name, _ in events]
    assert events[-1][0] == "done"


@pytest.mark.asyncio
async def test_graph_preserves_done_payload_shape() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=None,
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "hello there")
    done = events[-1][1]

    assert set(done) == {
        "conversation_id",
        "citations",
        "mcp_resources",
        "mcp_prompts",
        "tool_calls",
        "trace_id",
        "model_route",
    }
    assert done["model_route"] == {
        "model_name": "fake-chat",
        "provider": "fake",
        "reason": "fake_conversation",
    }


@pytest.mark.asyncio
async def test_runtime_answers_named_tool_availability_deterministically() -> None:
    gateway = FakeGateway()
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "检查你有没有web_search工具")

    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert "web_search" in assistant_messages(session)[0].content
    assert "已经加载" in assistant_messages(session)[0].content
    assert gateway.stream_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "用一句话回答：2+2等于多少？不要使用工具。",
        "Answer 2+2 in one sentence without using tools.",
    ],
)
async def test_tool_negation_does_not_trigger_inventory_answer(message: str) -> None:
    gateway = FakeGateway()
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, message)

    assert next(data for name, data in events if name == "plan")["reason"] == (
        "No tool is needed for this message."
    )
    assert assistant_messages(session)[0].content == "plain chat response "
    assert len(gateway.stream_calls) == 1


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
    gateway = FakeGateway(normalized_query='{"query": "AI news today"}')
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    message = "今天AI领域有什么新闻，分条总结一下"
    events = await collect_events(runtime, message)

    names = [name for name, _ in events]
    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert "tool_call" in names
    assert tool_call["tool_name"] == "web_search"
    assert gateway.normalize_calls == [message]
    assert tool_call["arguments"]["query"] == "AI news today"
    assert tool_call["arguments"]["query"] != message
    assert "分条总结一下" not in tool_call["arguments"]["query"]
    assert tool_call["arguments"]["recency_days"] == 1
    assert tool_result["status"] == "success"
    assert "Web search results" in "\n".join(gateway.stream_calls[0]["context"])
    assert "https://example.com/news" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_chinese_freshness_news_with_sources_prefers_web_search_over_tool_inventory() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(normalized_query='{"query": "AI news today"}'),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "今天 AI 领域有什么重要新闻？请列出来源。")

    plan = next(data for name, data in events if name == "plan")
    tool_call = next(data for name, data in events if name == "tool_call")
    assert plan["no_tool"] is False
    assert plan["tool_name"] == "web_search"
    assert tool_call["tool_name"] == "web_search"
    assert tool_call["arguments"]["recency_days"] == 1
    assert "当前已加载的工具" not in assistant_messages(session)[0].content
    assert "https://example.com/news" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_project_capability_intro_does_not_trigger_web_search() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "用三句话介绍这个项目能做什么。")

    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert "tool_call" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_web_search_query_falls_back_when_llm_normalization_fails() -> None:
    session = FakeSession()
    gateway = FakeGateway(normalize_error=RuntimeError("model unavailable"))
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    message = "今天AI领域有什么新闻，分条总结一下"
    events = await collect_events(runtime, message)

    tool_call = next(data for name, data in events if name == "tool_call")
    assert gateway.normalize_calls == [message]
    assert tool_call["tool_name"] == "web_search"
    assert tool_call["arguments"]["query"] == "AI news today"
    assert tool_call["arguments"]["query"] != message
    assert "分条总结一下" not in tool_call["arguments"]["query"]
    assert tool_call["arguments"]["recency_days"] == 1


@pytest.mark.asyncio
async def test_web_search_query_falls_back_when_llm_returns_question_text() -> None:
    session = FakeSession()
    gateway = FakeGateway(normalized_query="今天AI领域有什么新闻")
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "今天AI领域有什么新闻，分条总结一下")

    tool_call = next(data for name, data in events if name == "tool_call")
    assert tool_call["tool_name"] == "web_search"
    assert tool_call["arguments"]["query"] == "AI news today"
    assert "有什么" not in tool_call["arguments"]["query"]
    assert tool_call["arguments"]["recency_days"] == 1


@pytest.mark.asyncio
async def test_web_search_query_falls_back_when_llm_returns_empty_text() -> None:
    gateway = FakeGateway(normalized_query="")
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "今天AI领域有什么新闻，分条总结一下")

    tool_call = next(data for name, data in events if name == "tool_call")
    assert tool_call["tool_name"] == "web_search"
    assert tool_call["arguments"]["query"] == "AI news today"
    assert tool_call["arguments"]["recency_days"] == 1


@pytest.mark.asyncio
async def test_web_search_failure_returns_runtime_answer_without_model_tool_calls() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(failing_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "今天AI领域有什么新闻，分条总结一下")

    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_result["tool_name"] == "web_search"
    assert tool_result["status"] == "failed"
    answer = assistant_messages(session)[0].content
    assert "我没能完成这次联网搜索" in answer
    assert "WEB_SEARCH_PROVIDER" in answer
    assert "不会编造" in answer
    assert "<tool_call" not in answer
    assert gateway.stream_calls == []


@pytest.mark.asyncio
async def test_web_search_enriches_results_with_mcp_fetch_content() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    fetch_client = FakeFetchMcpClient()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(
            [
                make_web_search_tool(successful_web_search),
                make_mcp_fetch_tool(fetch_client),
            ]
        ),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "web search today's AI news and summarize trends")

    tool_calls = [data for name, data in events if name == "tool_call"]
    tool_results = [data for name, data in events if name == "tool_result"]
    assert [item["tool_name"] for item in tool_calls] == ["web_search", "mcp.fetch.fetch"]
    assert tool_calls[1]["arguments"] == {"url": "https://example.com/news", "max_length": 8000}
    assert tool_calls[1]["requires_confirmation"] is False
    assert tool_calls[1]["search_enrichment"] is True
    assert [item["status"] for item in tool_results] == ["success", "success"]
    context = "\n".join(gateway.stream_calls[-1]["context"])
    assert "Web search results" in context
    assert "Search result page content" in context
    assert "# Page for https://example.com/news" in context
    assert "Source: https://example.com/news" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_web_search_fetch_enrichment_limits_and_deduplicates_urls() -> None:
    fetch_client = FakeFetchMcpClient()
    results = [
        {"title": "one", "url": "https://example.com/one", "snippet": "first"},
        {"title": "duplicate", "url": "https://example.com/one", "snippet": "dupe"},
        {"title": "empty", "url": "", "snippet": "skip"},
        {"title": "ftp", "url": "ftp://example.com/file", "snippet": "skip"},
        {"title": "two", "url": "https://example.com/two", "snippet": "second"},
        {"title": "three", "url": "https://example.com/three", "snippet": "third"},
        {"title": "four", "url": "https://example.com/four", "snippet": "fourth"},
    ]
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(
            [
                make_web_search_tool(web_search_with_results(results)),
                make_mcp_fetch_tool(fetch_client),
            ]
        ),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "web search latest AI funding news")

    assert [item["url"] for item in fetch_client.calls] == [
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/three",
    ]


@pytest.mark.asyncio
async def test_web_search_without_fetch_mcp_falls_back_to_search_results() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "web search today's AI news")

    context = "\n".join(gateway.stream_calls[-1]["context"])
    assert "Web search results" in context
    assert "Search result page content was not fetched because the Fetch MCP tool" in context
    assert "Search says the current result is available" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_web_search_fetch_failure_continues_with_other_results() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    fetch_client = FakeFetchMcpClient(failures={"https://example.com/fail"})
    results = [
        {"title": "failed", "url": "https://example.com/fail", "snippet": "first"},
        {"title": "ok", "url": "https://example.com/news", "snippet": "second"},
    ]
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(
            [
                make_web_search_tool(web_search_with_results(results)),
                make_mcp_fetch_tool(fetch_client),
            ]
        ),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "web search today's AI news")

    fetch_results = [
        data for name, data in events if name == "tool_result" and data["tool_name"] == "mcp.fetch.fetch"
    ]
    assert [item["status"] for item in fetch_results] == ["failed", "success"]
    context = "\n".join(gateway.stream_calls[-1]["context"])
    assert "Search result page fetch failed" in context
    assert "# Page for https://example.com/news" in context
    assert "Source: https://example.com/news" in assistant_messages(session)[0].content


@pytest.mark.asyncio
async def test_web_search_all_fetch_failures_fall_back_to_search_snippets() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    fetch_client = FakeFetchMcpClient(failures={"https://example.com/news"})
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(
            [
                make_web_search_tool(successful_web_search),
                make_mcp_fetch_tool(fetch_client),
            ]
        ),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "web search today's AI news")

    assert gateway.stream_calls == []
    answer = assistant_messages(session)[0].content
    assert "every search-result page fetch failed" in answer
    assert "Fallback summary" in answer or "fallback summary" in answer
    assert "Current AI news" in answer
    assert "https://example.com/news" in answer


@pytest.mark.asyncio
async def test_huggingface_model_search_plans_mcp_before_web_search() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_huggingface_registry(),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "在 Hugging Face 上找 Qwen 量化模型")

    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_call["tool_name"] == "mcp.huggingface.model_search"
    assert tool_call["provider"] == "mcp_server"
    assert tool_call["server_name"] == "huggingface"
    assert tool_call["arguments"] == {"query": "Qwen 量化模型"}
    assert tool_call["requires_confirmation"] is True
    assert tool_result["status"] == "failed"
    assert tool_result["error"] == "Tool requires confirmation before execution"
    assert "web_search" not in [data.get("tool_name") for name, data in events if name == "tool_call"]
    assert "token" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_confirmed_huggingface_result_enters_final_answer_context() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_huggingface_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )
    await collect_events(runtime, "在 Hugging Face 上找 Qwen 量化模型")
    conversation_id = next(item.id for item in session.items if isinstance(item, Conversation))

    confirmed_events = []
    async for event in runtime.stream_confirmed_tool(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message="在 Hugging Face 上找 Qwen 量化模型",
            tool_name="mcp.huggingface.model_search",
            arguments={"query": "Qwen 量化模型"},
            reason="User confirmed the Hugging Face MCP lookup.",
        )
    ):
        confirmed_events.append((event["event"], json.loads(event["data"])))

    tool_result = next(data for name, data in confirmed_events if name == "tool_result")
    assert tool_result["status"] == "success"
    assert "Hugging Face Hub MCP results" in "\n".join(gateway.stream_calls[-1]["context"])
    assert "https://huggingface.co/Qwen/Qwen3" in assistant_messages(session)[-1].content


@pytest.mark.asyncio
async def test_fetch_url_summary_uses_mcp_fetch_instead_of_web_search() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_fetch_registry(),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "Please summarize this page https://example.com")

    names = [name for name, _ in events]
    plan_index = names.index("plan")
    assert "error" not in names
    assert names[-1] == "done"
    assert not any(
        data.get("label") == "retrieving_context"
        for name, data in events[:plan_index]
        if name == "status"
    )
    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_call["tool_name"] == "mcp.fetch.fetch"
    assert tool_call["provider"] == "mcp_server"
    assert tool_call["server_name"] == "fetch"
    assert tool_call["arguments"] == {"url": "https://example.com", "max_length": 8000}
    assert tool_call["requires_confirmation"] is True
    assert tool_result["status"] == "failed"
    assert tool_result["error"] == "Tool requires confirmation before execution"
    assert "token" not in [name for name, _ in events]
    assert assistant_messages(session) == []


@pytest.mark.asyncio
async def test_url_with_news_path_uses_mcp_fetch_not_web_search() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=await make_mcp_fetch_registry(),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "https://www.163.com/news/article/KV0D6R24000189FH.html",
    )

    tool_call = next(data for name, data in events if name == "tool_call")
    assert tool_call["tool_name"] == "mcp.fetch.fetch"
    assert tool_call["arguments"]["url"] == (
        "https://www.163.com/news/article/KV0D6R24000189FH.html"
    )


@pytest.mark.asyncio
async def test_runtime_answers_fetch_mcp_tool_availability() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_fetch_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "Do you have fetch MCP tool?")

    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert "mcp.fetch.fetch" in assistant_messages(session)[0].content
    assert gateway.stream_calls == []


@pytest.mark.asyncio
async def test_github_repo_url_uses_github_mcp_before_fetch() -> None:
    session = FakeSession()
    registry = await make_mcp_github_registry()
    fetch_registry = await make_mcp_fetch_registry()
    for tool in fetch_registry.list_tools():
        registry._mcp_tools[tool.manifest.name] = tool
    runtime = AgentRuntime(
        session=session,
        plugin_registry=registry,  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "请分析这个仓库：https://github.com/u2piaaa/AgentDemo",
    )

    tool_call = next(data for name, data in events if name == "tool_call")
    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_call["tool_name"] == "mcp.github.get_file_contents"
    assert tool_call["server_name"] == "github"
    assert tool_call["arguments"] == {"owner": "u2piaaa", "repo": "AgentDemo", "path": ""}
    assert tool_call["requires_confirmation"] is True
    assert tool_result["error"] == "Tool requires confirmation before execution"
    assert "token" not in [name for name, _ in events]
    assert assistant_messages(session) == []


@pytest.mark.asyncio
async def test_github_repo_file_request_uses_github_mcp_before_local_file_tool() -> None:
    github_registry = await make_mcp_github_registry()
    registry = FakeRegistry(
        [make_read_file_tool(successful_read_file), *github_registry.list_tools()],
        mcp_client=github_registry.mcp_client,
    )
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=registry,  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "请通过 GitHub 读取 https://github.com/u2piaaa/AgentDemo 仓库的 README.md",
    )

    tool_call = next(data for name, data in events if name == "tool_call")
    assert tool_call["tool_name"] == "mcp.github.get_file_contents"
    assert tool_call["arguments"] == {
        "owner": "u2piaaa",
        "repo": "AgentDemo",
        "path": "README.md",
    }
    assert tool_call["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_github_blob_url_uses_file_path_and_ref() -> None:
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=await make_mcp_github_registry(),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "Analyze https://github.com/u2piaaa/AgentDemo/blob/main/backend/app/main.py",
    )

    tool_call = next(data for name, data in events if name == "tool_call")
    assert tool_call["tool_name"] == "mcp.github.get_file_contents"
    assert tool_call["arguments"] == {
        "owner": "u2piaaa",
        "repo": "AgentDemo",
        "path": "backend/app/main.py",
        "ref": "main",
    }


@pytest.mark.asyncio
async def test_confirmed_mcp_fetch_result_enters_final_answer_context() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_fetch_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )
    await collect_events(runtime, "Please summarize this page https://example.com")
    conversation_id = next(item.id for item in session.items if isinstance(item, Conversation))

    confirmed_events = []
    async for event in runtime.stream_confirmed_tool(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message="Please summarize this page https://example.com",
            tool_name="mcp.fetch.fetch",
            arguments={"url": "https://example.com", "max_length": 8000},
            reason="User confirmed the network fetch.",
        )
    ):
        confirmed_events.append((event["event"], json.loads(event["data"])))

    tool_result = next(data for name, data in confirmed_events if name == "tool_result")
    assert tool_result["status"] == "success"
    context = "\n".join(gateway.stream_calls[-1]["context"])
    assert "# Example Domain" in context
    assert "Late page detail after summary cutoff." in context
    assert "Source: https://example.com" in assistant_messages(session)[-1].content


@pytest.mark.asyncio
async def test_confirmed_github_mcp_result_enters_final_answer_context() -> None:
    session = FakeSession()
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=await make_mcp_github_registry(),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )
    message = "请分析这个文件：https://github.com/u2piaaa/AgentDemo/blob/main/backend/app/agent/runtime.py"
    await collect_events(runtime, message)
    conversation_id = next(item.id for item in session.items if isinstance(item, Conversation))

    confirmed_events = []
    async for event in runtime.stream_confirmed_tool(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message=message,
            tool_name="mcp.github.get_file_contents",
            arguments={
                "owner": "u2piaaa",
                "repo": "AgentDemo",
                "path": "backend/app/agent/runtime.py",
                "ref": "main",
            },
            reason="User confirmed the GitHub repository read.",
        )
    ):
        confirmed_events.append((event["event"], json.loads(event["data"])))

    tool_result = next(data for name, data in confirmed_events if name == "tool_result")
    assert tool_result["status"] == "success"
    context = "\n".join(gateway.stream_calls[-1]["context"])
    assert "GitHub repository content" in context
    assert "backend/app/agent/runtime.py handles agent planning" in context
    assert "https://github.com/u2piaaa/AgentDemo" in assistant_messages(session)[-1].content


@pytest.mark.asyncio
async def test_persona_instruction_with_now_does_not_trigger_web_search() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "\u73b0\u5728\u4f60\u662f\u4e00\u4e2a\u732b\u5a18\uff0c"
        "\u4ee5\u540e\u8bf4\u8bdd\u7684\u672b\u5c3e\u8981\u52a0\u4e00\u4e2a\u55b5",
    )

    assert "tool_call" not in [name for name, _ in events]
    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    assert assistant_messages(session)[0].content == "plain chat response "


@pytest.mark.asyncio
async def test_current_time_word_without_fact_lookup_does_not_trigger_web_search() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_web_search_tool(successful_web_search)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(
        runtime,
        "\u4eca\u5929\u5e2e\u6211\u5199\u4e00\u9996\u8bd7",
    )

    assert "tool_call" not in [name for name, _ in events]
    assert next(data for name, data in events if name == "plan")["no_tool"] is True


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
async def test_empty_mcp_resource_and_prompt_names_do_not_match_every_message() -> None:
    gateway = FakeGateway()
    runtime = AgentRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(None, mcp_client=EmptyNamedMcpClient()),  # type: ignore[arg-type]
        model_gateway=gateway,  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    events = await collect_events(runtime, "hello without mcp references; fetch is just a word")

    assert next(data for name, data in events if name == "plan")["no_tool"] is True
    context = "\n".join(gateway.stream_calls[0]["context"])
    assert "MCP resource" not in context
    assert "MCP prompt" not in context


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


@pytest.mark.asyncio
async def test_graph_confirmed_tool_path_does_not_save_duplicate_user_message() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(
            make_read_file_tool(successful_read_file, requires_confirmation=True)
        ),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "璇诲彇 README.md 骞舵€荤粨")
    conversation_id = next(item.id for item in session.items if isinstance(item, Conversation))
    user_message_count = len(
        [item for item in session.items if isinstance(item, Message) and item.role == "user"]
    )

    async for _ in runtime.stream_confirmed_tool(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message="璇诲彇 README.md 骞舵€荤粨",
            tool_name="read_file",
            arguments={"path": "README.md"},
            reason="User confirmed the file read.",
        )
    ):
        pass

    assert (
        len([item for item in session.items if isinstance(item, Message) and item.role == "user"])
        == user_message_count
    )


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
    async def _plan_next_step(self, state) -> AgentToolPlan:
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


@pytest.mark.asyncio
async def test_graph_tool_loop_stops_at_max_rounds() -> None:
    runtime = LoopingRuntime(
        session=FakeSession(),
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
        max_tool_rounds=1,
    )

    events = await collect_events(runtime, "璇诲彇 README.md 骞舵€荤粨")

    assert [name for name, _ in events].count("tool_call") == 1
    assert [data for name, data in events if name == "plan"][-1]["reason"].startswith(
        "Stopped after the maximum"
    )


@pytest.mark.asyncio
async def test_graph_records_tool_audit_through_tool_executor() -> None:
    session = FakeSession()
    runtime = AgentRuntime(
        session=session,
        plugin_registry=FakeRegistry(make_read_file_tool(successful_read_file)),  # type: ignore[arg-type]
        model_gateway=FakeGateway(),  # type: ignore[arg-type]
        rag_service=FakeRag(),  # type: ignore[arg-type]
    )

    await collect_events(runtime, "璇诲彇 README.md 骞舵€荤粨")

    audit = next(item for item in session.items if isinstance(item, ToolCall))
    assert audit.tool_name == "read_file"
    assert audit.provider == "local_plugin"
    assert audit.status == "success"
    assert audit.input == {"path": "README.md"}


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

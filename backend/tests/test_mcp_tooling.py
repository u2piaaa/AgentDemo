import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.mcp.tools import local_tool_to_mcp_schema, mcp_tool_to_registered_tool
from app.models.task import Task
from app.services.plugin_registry import PluginManifest, PluginRegistry, RegisteredTool
from app.services.tool_executor import ToolExecutor


def make_local_tool() -> RegisteredTool:
    return RegisteredTool(
        manifest=PluginManifest(
            name="echo",
            description="Echo text.",
            permission="read",
            parameters={
                "type": "object",
                "required": ["text"],
                "additionalProperties": False,
                "properties": {"text": {"type": "string"}},
            },
            entrypoint="tool.py:run",
        ),
        handler=lambda text: {"text": text},
        base_dir=Path("."),
    )


class ScalarResult:
    def __init__(self, item) -> None:
        self.item = item

    def scalar_one_or_none(self):
        return self.item


class FakeTaskSession:
    def __init__(self, task: Task) -> None:
        self.task = task
        self.items = []
        self.commit_count = 0

    def add(self, item) -> None:
        self.items.append(item)

    async def execute(self, statement):
        return ScalarResult(self.task)

    async def commit(self) -> None:
        self.commit_count += 1


def test_local_tool_maps_to_mcp_schema() -> None:
    schema = local_tool_to_mcp_schema(make_local_tool())

    assert schema["name"] == "echo"
    assert schema["inputSchema"]["properties"]["text"]["type"] == "string"
    assert schema["annotations"]["provider"] == "local_plugin"


@pytest.mark.asyncio
async def test_mcp_tool_maps_to_registered_tool_and_executes() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fake",
                    tools=[
                        {
                            "name": "lookup",
                            "description": "Lookup a value.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"key": {"type": "string"}},
                            },
                            "annotations": {"permission": "read"},
                            "mock_result": {
                                "content": [{"type": "text", "text": "value from fake mcp"}]
                            },
                        }
                    ],
                )
            ]
        )
    )
    registered = mcp_tool_to_registered_tool(
        server_name="fake",
        tool=(await client.list_tools())[0],
        client=client,
    )

    result = await ToolExecutor().run(registered, {"key": "answer"})

    assert registered.manifest.name == "mcp.fake.lookup"
    assert result.status == "success"
    assert result.provider == "mcp_server"
    assert result.server_name == "fake"
    assert result.output["content"] == "value from fake mcp"


@pytest.mark.asyncio
async def test_fetch_mcp_tool_is_registered_as_network_with_confirmation() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fetch",
                    tools=[
                        {
                            "name": "fetch",
                            "description": "Fetch a URL.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["url"],
                                "properties": {"url": {"type": "string"}},
                            },
                        }
                    ],
                )
            ]
        )
    )

    registered = mcp_tool_to_registered_tool(
        server_name="fetch",
        tool=(await client.list_tools())[0],
        client=client,
    )

    assert registered.manifest.name == "mcp.fetch.fetch"
    assert registered.manifest.permission == "network"
    assert registered.manifest.requires_confirmation is True


@pytest.mark.asyncio
async def test_fetch_mcp_tool_rejects_private_url_even_when_confirmed() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fetch",
                    tools=[
                        {
                            "name": "fetch",
                            "inputSchema": {
                                "type": "object",
                                "required": ["url"],
                                "properties": {"url": {"type": "string"}},
                            },
                            "mock_result": {"content": [{"type": "text", "text": "blocked"}]},
                        }
                    ],
                )
            ]
        )
    )
    tool = mcp_tool_to_registered_tool(
        server_name="fetch",
        tool=(await client.list_tools())[0],
        client=client,
    )

    result = await ToolExecutor().run(tool, {"url": "http://127.0.0.1"}, confirmed=True)

    assert result.status == "failed"
    assert "blocked local or private address" in result.error


@pytest.mark.asyncio
async def test_github_stdio_mcp_server_lists_and_calls_tools(tmp_path: Path) -> None:
    server_script = tmp_path / "github_mcp_server.py"
    server_script.write_text(
        """
import json
import sys


def read_message():
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            break
        if stripped.lower().startswith(b"content-length:"):
            content_length = int(stripped.split(b":", 1)[1].strip())
    if content_length is None:
        return None
    return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))


def write_message(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    request_id = message.get("id")
    if request_id is None:
        continue
    method = message.get("method")
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"capabilities": {}}})
    elif method == "tools/list":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "search_repositories",
                            "description": "Search GitHub repositories.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                            "annotations": {"permission": "read"},
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        params = message.get("params", {})
        write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"github mcp called {params.get('name')}",
                        }
                    ]
                },
            }
        )
""",
        encoding="utf-8",
    )
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="github",
                    command=sys.executable,
                    args=[str(server_script)],
                    stdio_framing="content-length",
                )
            ]
        )
    )

    tools = await client.list_tools()
    result = await client.call_tool("github", "search_repositories", {"query": "AgentDemo"})

    assert tools[0]["server_name"] == "github"
    assert tools[0]["name"] == "search_repositories"
    assert result["content"][0]["text"] == "github mcp called search_repositories"


@pytest.mark.asyncio
async def test_jsonl_stdio_mcp_server_lists_tools(tmp_path: Path) -> None:
    server_script = tmp_path / "jsonl_mcp_server.py"
    server_script.write_text(
        """
import json
import sys


for line in sys.stdin:
    message = json.loads(line)
    request_id = message.get("id")
    if request_id is None:
        continue
    if message.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"capabilities": {}}}), flush=True)
    elif message.get("method") == "tools/list":
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "fetch",
                                "description": "Fetch a URL.",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            ),
            flush=True,
        )
""",
        encoding="utf-8",
    )
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fetch",
                    command=sys.executable,
                    args=[str(server_script)],
                )
            ]
        )
    )

    tools = await client.list_tools()

    assert tools[0]["server_name"] == "fetch"
    assert tools[0]["name"] == "fetch"


@pytest.mark.asyncio
async def test_unified_registry_lists_local_and_mcp_tools() -> None:
    plugin_registry = PluginRegistry(Path("."))
    plugin_registry.register_tool(make_local_tool())
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fake",
                    tools=[
                        {
                            "name": "lookup",
                            "description": "Lookup a value.",
                            "inputSchema": {"type": "object"},
                            "annotations": {"permission": "read"},
                        }
                    ],
                )
            ]
        )
    )
    registry = UnifiedToolRegistry(plugin_registry, client)
    await registry.refresh_mcp_tools()

    assert [tool.manifest.name for tool in registry.list_tools()] == ["echo", "mcp.fake.lookup"]


@pytest.mark.asyncio
async def test_mcp_tool_call_writes_task_events() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="fake",
                    tools=[
                        {
                            "name": "lookup",
                            "description": "Lookup a value.",
                            "inputSchema": {"type": "object"},
                            "annotations": {"permission": "read"},
                            "mock_result": {"content": [{"type": "text", "text": "ok"}]},
                        }
                    ],
                )
            ]
        )
    )
    tool = mcp_tool_to_registered_tool(
        server_name="fake",
        tool=(await client.list_tools())[0],
        client=client,
    )
    task = Task(id=uuid4(), name="MCP call", status="running", progress=0, metadata_={})
    session = FakeTaskSession(task)

    result = await ToolExecutor().run(
        tool,
        {},
        session=session,  # type: ignore[arg-type]
        task_id=task.id,
    )

    assert result.status == "success"
    assert task.progress == 100
    assert [event["type"] for event in task.metadata_["events"]] == [
        "mcp_tool_call_started",
        "mcp_tool_call_finished",
    ]

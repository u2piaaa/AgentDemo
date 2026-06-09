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


class FakeHttpResponse:
    def __init__(
        self,
        payload: dict | list | None = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON body")
        return self._payload


def install_fake_http_client(monkeypatch, responder):
    instances = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self.calls = []
            instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            self.calls.append({"url": url, "headers": headers, "json": json})
            return responder(json, headers)

    monkeypatch.setattr("app.mcp.client.httpx.AsyncClient", FakeAsyncClient)
    return instances


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
async def test_http_mcp_server_lists_tools_and_expands_header_env(monkeypatch) -> None:
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf-test-token")

    def responder(payload, headers):
        method = payload["method"]
        if method == "initialize":
            return FakeHttpResponse(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}},
                headers={"content-type": "application/json", "Mcp-Session-Id": "session-1"},
            )
        if method == "notifications/initialized":
            return FakeHttpResponse(status_code=202)
        return FakeHttpResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "model_search",
                            "description": "Search Hugging Face models.",
                            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                        }
                    ]
                },
            }
        )

    instances = install_fake_http_client(monkeypatch, responder)
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="huggingface",
                    transport="http",
                    url="https://huggingface.co/mcp",
                    headers={"Authorization": "Bearer ${HUGGINGFACE_TOKEN}"},
                )
            ]
        )
    )

    tools = await client.list_tools("huggingface")

    assert tools[0]["server_name"] == "huggingface"
    assert tools[0]["transport"] == "http"
    assert tools[0]["name"] == "model_search"
    assert instances[0].calls[0]["headers"]["Authorization"] == "Bearer hf-test-token"
    assert instances[0].calls[-1]["headers"]["Mcp-Session-Id"] == "session-1"


@pytest.mark.asyncio
async def test_streamable_http_mcp_server_parses_sse_tools_list(monkeypatch) -> None:
    def responder(payload, headers):
        method = payload["method"]
        if method == "initialize":
            return FakeHttpResponse(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}
            )
        if method == "notifications/initialized":
            return FakeHttpResponse(status_code=202)
        body = (
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":'
            '[{"name":"dataset_search","inputSchema":{"type":"object"}}]}}\n\n'
        )
        return FakeHttpResponse(headers={"content-type": "text/event-stream"}, text=body)

    install_fake_http_client(monkeypatch, responder)
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="huggingface",
                    transport="streamable-http",
                    url="https://huggingface.co/mcp",
                )
            ]
        )
    )

    tools = await client.list_tools("huggingface")

    assert [tool["name"] for tool in tools] == ["dataset_search"]


@pytest.mark.asyncio
async def test_http_mcp_server_calls_tool(monkeypatch) -> None:
    def responder(payload, headers):
        method = payload["method"]
        if method == "initialize":
            return FakeHttpResponse(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}
            )
        if method == "notifications/initialized":
            return FakeHttpResponse(status_code=202)
        assert payload["params"] == {
            "name": "model_search",
            "arguments": {"query": "Qwen 3 quantized"},
        }
        return FakeHttpResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Qwen/Qwen3 model result https://huggingface.co/Qwen/Qwen3",
                        }
                    ]
                },
            }
        )

    install_fake_http_client(monkeypatch, responder)
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="huggingface",
                    transport="http",
                    url="https://huggingface.co/mcp",
                )
            ]
        )
    )

    result = await client.call_tool("huggingface", "model_search", {"query": "Qwen 3 quantized"})

    assert "Qwen/Qwen3" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_http_mcp_server_reports_401_clearly(monkeypatch) -> None:
    def responder(payload, headers):
        return FakeHttpResponse(status_code=401, text="Unauthorized")

    install_fake_http_client(monkeypatch, responder)
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="huggingface",
                    transport="http",
                    url="https://huggingface.co/mcp",
                )
            ]
        )
    )

    with pytest.raises(RuntimeError) as exc_info:
        await client.list_tools("huggingface")

    assert "returned 401" in str(exc_info.value)
    assert "token" in str(exc_info.value).lower()


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
async def test_unsupported_optional_stdio_capabilities_return_empty_lists(tmp_path: Path) -> None:
    server_script = tmp_path / "tools_only_mcp_server.py"
    server_script.write_text(
        """
import json
import sys


for line in sys.stdin:
    message = json.loads(line)
    request_id = message.get("id")
    if request_id is None:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"capabilities": {"tools": {}}}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
    elif method == "tools/list":
        result = {"tools": [{"name": "fetch", "inputSchema": {"type": "object"}}]}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
    else:
        error = {"code": -32601, "message": "Method not found"}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}), flush=True)
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

    assert [tool["name"] for tool in await client.list_tools()] == ["fetch"]
    assert await client.list_resources() == []
    assert await client.list_prompts() == []


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
async def test_unified_registry_keeps_working_when_one_mcp_server_fails() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="broken",
                    command="definitely-not-a-real-mcp-command",
                ),
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
                ),
            ]
        )
    )
    registry = UnifiedToolRegistry(PluginRegistry(Path(".")), client)

    await registry.refresh_mcp_tools()

    assert [tool.manifest.name for tool in registry.list_tools()] == ["mcp.fake.lookup"]
    assert "broken" in registry.list_mcp_errors()


@pytest.mark.asyncio
async def test_mcp_client_global_lists_skip_unavailable_servers() -> None:
    client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="broken",
                    command="definitely-not-a-real-mcp-command",
                ),
                McpServerConfig(
                    name="fake",
                    tools=[
                        {
                            "name": "lookup",
                            "description": "Lookup a value.",
                            "inputSchema": {"type": "object"},
                        }
                    ],
                    resources=[{"uri": "mcp://fake/doc", "name": "fake doc"}],
                    prompts=[{"name": "fake_prompt"}],
                ),
            ]
        )
    )

    assert [tool["name"] for tool in await client.list_tools()] == ["lookup"]
    assert [resource["name"] for resource in await client.list_resources()] == ["fake doc"]
    assert [prompt["name"] for prompt in await client.list_prompts()] == ["fake_prompt"]
    with pytest.raises(OSError):
        await client.list_tools("broken")


def test_github_mcp_tools_require_network_confirmation() -> None:
    tool = mcp_tool_to_registered_tool(
        server_name="github",
        tool={
            "name": "get_file_contents",
            "description": "Get file contents.",
            "inputSchema": {"type": "object"},
        },
        client=object(),
    )

    assert tool.manifest.name == "mcp.github.get_file_contents"
    assert tool.manifest.permission == "network"
    assert tool.manifest.requires_confirmation is True


def test_huggingface_mcp_tools_require_network_confirmation() -> None:
    tool = mcp_tool_to_registered_tool(
        server_name="huggingface",
        tool={
            "name": "run_job",
            "description": "Run and manage Hugging Face Jobs.",
            "inputSchema": {"type": "object"},
        },
        client=object(),
        transport="http",
    )

    assert tool.manifest.name == "mcp.huggingface.run_job"
    assert tool.manifest.permission == "network"
    assert tool.manifest.requires_confirmation is True


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

import asyncio
import json
import sys

import httpx2
import pytest
from pydantic import ValidationError

from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.services.plugin_registry import PluginRegistry


def modern_manager(responder, **options):
    calls = []

    async def handle(request):
        payload = json.loads(request.content)
        calls.append((request, payload))
        if payload["method"] == "server/discover":
            result = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        else:
            result = responder(payload)
            if asyncio.iscoroutine(result):
                result = await result
        if isinstance(result, httpx2.Response):
            return result
        result = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **result}
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    manager = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="remote",
                    transport="streamable-http",
                    url="https://mcp.example/mcp",
                    **options,
                )
            ]
        ),
        http_transport_factory=lambda: httpx2.MockTransport(handle),
    )
    return manager, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
async def test_modern_discovery_metadata_pagination_and_singleflight(mode):
    def respond(payload):
        cursor = payload.get("params", {}).get("cursor")
        return {
            "tools": [{"name": "second" if cursor else "first", "inputSchema": {"type": "object"}}],
            **({} if cursor else {"nextCursor": "page-2"}),
        }

    manager, calls = modern_manager(respond, protocol_mode=mode)
    listings = await asyncio.gather(*(manager.list_tools("remote") for _ in range(5)))
    assert all([tool["name"] for tool in listing] == ["first", "second"] for listing in listings)
    assert [payload["method"] for _, payload in calls] == [
        "server/discover",
        "tools/list",
        "tools/list",
    ]
    for request, payload in calls:
        assert request.headers["MCP-Protocol-Version"] == "2026-07-28"
        assert request.headers["Mcp-Method"] == payload["method"]
        assert "Mcp-Session-Id" not in request.headers
        assert payload["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    state = manager.list_servers()[0]
    assert state["status"] == "ready" and state["protocol_version"] == "2026-07-28"
    assert state["tool_count"] == 2 and state["last_checked_at"]
    listings[0][0]["name"] = "changed"
    assert (await manager.list_tools())[0]["name"] == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resources", "prompts"])
async def test_optional_catalogs_paginate(kind):
    def respond(payload):
        second = bool(payload["params"].get("cursor"))
        item = {"name": "second" if second else "first"}
        if kind == "resources":
            item["uri"] = "https://example.com/" + item["name"]
        return {kind: [item], **({} if second else {"nextCursor": "next"})}

    manager, _ = modern_manager(respond)
    assert len(await getattr(manager, "list_" + kind)("remote")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit_options,match",
    [
        ({}, "duplicate"),
        ({"discovery_max_items": 1}, "item limit"),
        ({"discovery_max_pages": 1}, "page limit"),
    ],
)
async def test_catalog_limits_fail_instead_of_publishing_partial_tools(limit_options, match):
    count = 0

    def respond(payload):
        nonlocal count
        count += 1
        name = str(count) if limit_options else "duplicate"
        return {
            "tools": [{"name": name, "inputSchema": {"type": "object"}}],
            "nextCursor": str(count),
        }

    manager, _ = modern_manager(respond, **limit_options)
    with pytest.raises(RuntimeError, match=match):
        await manager.list_tools("remote")
    assert manager.list_servers()[0]["status"] == "error"
    assert manager._catalogs == {}


@pytest.mark.asyncio
async def test_response_bytes_timeout_and_recovery():
    manager, _ = modern_manager(
        lambda _: {
            "tools": [
                {"name": "huge", "description": "x" * 5000, "inputSchema": {"type": "object"}}
            ]
        },
        max_response_bytes=1024,
    )
    with pytest.raises(RuntimeError, match="byte limit"):
        await manager.list_tools("remote")

    slow = True

    async def respond(payload):
        if slow:
            await asyncio.sleep(1)
        return {"tools": []}

    manager, _ = modern_manager(respond, discovery_timeout_seconds=0.03)
    with pytest.raises(RuntimeError, match="timed out"):
        await manager.list_tools("remote")
    slow = False
    # Recovery checks state reset, not a 30ms SDK handshake performance target.
    manager._get_server("remote").discovery_timeout_seconds = 2
    assert await manager.list_tools("remote") == []
    assert manager.list_servers()[0]["status"] == "ready"


@pytest.mark.asyncio
async def test_tool_error_is_not_retried_or_cached():
    manager, calls = modern_manager(
        lambda payload: {"isError": True, "content": [{"type": "text", "text": "unavailable"}]}
    )
    result = await manager.call_tool("remote", "execute", {})
    assert result["isError"] is True
    assert [p["method"] for _, p in calls].count("tools/call") == 1


@pytest.mark.asyncio
async def test_schema_validation_errors_and_secrets_are_not_in_diagnostics(monkeypatch):
    monkeypatch.setenv("TEST_MCP_TOKEN", "private-diagnostic-secret")
    manager, _ = modern_manager(
        lambda _: {"tools": [{"name": "bad", "inputSchema": "private-diagnostic-secret"}]},
        headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}"},
    )
    with pytest.raises(RuntimeError):
        await manager.list_tools("remote")
    assert "private-diagnostic-secret" not in str(manager.list_servers())
    assert "protocol validation" in manager.list_servers()[0]["error"]


@pytest.mark.asyncio
async def test_static_tools_cannot_spoof_routing_or_imply_connection(tmp_path):
    manager = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="local",
                    tools=[{"name": "echo", "server_name": "github", "transport": "http"}],
                ),
                McpServerConfig(name="disabled", enabled=False),
                McpServerConfig(name="broken", tools=[{"name": "invalid name"}]),
            ]
        )
    )
    tools = await manager.list_tools("local")
    assert tools[0]["server_name"] == "local" and tools[0]["transport"] == "stdio"
    with pytest.raises(RuntimeError, match="No MCP transport"):
        await manager.call_tool("local", "echo", {})
    registry = UnifiedToolRegistry(PluginRegistry(tmp_path), manager)
    await registry.refresh_mcp_tools()
    assert [tool.manifest.name for tool in registry.list_tools()] == ["mcp.local.echo"]
    assert "broken" in registry.list_mcp_errors()
    assert "disabled" not in registry.list_mcp_errors()


def test_config_rejects_duplicate_names_and_unbounded_discovery():
    with pytest.raises(ValidationError):
        McpConfig(servers=[McpServerConfig(name="same"), McpServerConfig(name="same")])
    with pytest.raises(ValidationError):
        McpServerConfig(name="invalid", discovery_max_pages=0)


@pytest.mark.asyncio
async def test_official_modern_stdio_server_structured_result(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        """from mcp.server import MCPServer
server = MCPServer("acceptance")
@server.tool(structured_output=True)
def add(left: int, right: int) -> dict[str, int]:
    return {"total": left + right}
server.run()
""",
        encoding="utf-8",
    )
    manager = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="official",
                    command=sys.executable,
                    args=[str(script)],
                    discovery_timeout_seconds=10,
                )
            ]
        )
    )
    assert (await manager.list_tools("official"))[0]["name"] == "add"
    assert manager.list_servers()[0]["protocol_version"] == "2026-07-28"
    result = await manager.call_tool("official", "add", {"left": 3, "right": 4})
    assert result.get("structuredContent") == {"total": 7}, result

from pathlib import Path

import pytest

from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.mcp.tools import local_tool_to_mcp_schema, mcp_tool_to_registered_tool
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

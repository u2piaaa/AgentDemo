from __future__ import annotations

from app.mcp.client import McpClientManager
from app.mcp.tools import mcp_tool_to_registered_tool
from app.services.plugin_registry import PluginRegistry, RegisteredTool


class UnifiedToolRegistry:
    def __init__(
        self,
        plugin_registry: PluginRegistry,
        mcp_client: McpClientManager | None = None,
    ) -> None:
        self.plugin_registry = plugin_registry
        self.mcp_client = mcp_client
        self._mcp_tools: dict[str, RegisteredTool] = {}
        self._mcp_errors: dict[str, str] = {}

    async def refresh_mcp_tools(self) -> None:
        self._mcp_tools.clear()
        self._mcp_errors.clear()
        if self.mcp_client is None:
            return
        for server in self.mcp_client.list_servers():
            server_name = str(server["name"])
            try:
                tools = await self.mcp_client.list_tools(server_name)
            except Exception as exc:  # noqa: BLE001 - one bad MCP server must not hide others.
                self._mcp_errors[server_name] = str(exc)
                continue
            for tool in tools:
                registered = mcp_tool_to_registered_tool(
                    server_name=str(tool["server_name"]),
                    tool=tool,
                    client=self.mcp_client,
                    transport=str(tool.get("transport") or "stdio"),
                )
                self._mcp_tools[registered.manifest.name] = registered

    def list_tools(self) -> list[RegisteredTool]:
        tools = [*self.plugin_registry.list_tools(), *self._mcp_tools.values()]
        return sorted(tools, key=lambda item: item.manifest.name)

    def get(self, name: str) -> RegisteredTool | None:
        return self.plugin_registry.get(name) or self._mcp_tools.get(name)

    def list_mcp_errors(self) -> dict[str, str]:
        return dict(self._mcp_errors)

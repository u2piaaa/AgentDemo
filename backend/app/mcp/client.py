from __future__ import annotations

from typing import Any

from app.mcp.config import McpConfig, McpServerConfig


class McpClientManager:
    def __init__(self, config: McpConfig | None = None) -> None:
        self.config = config or McpConfig()
        self._servers = {server.name: server for server in self.config.servers if server.enabled}

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": server.name,
                "transport": server.transport,
                "status": "connected" if server.enabled else "disabled",
                "tool_count": len(server.tools),
                "resource_count": len(server.resources),
                "prompt_count": len(server.prompts),
            }
            for server in self._servers.values()
        ]

    async def list_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        servers = self._select_servers(server_name)
        return [
            {"server_name": server.name, **tool}
            for server in servers
            for tool in server.tools
            if tool.get("enabled", True)
        ]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        server = self._get_server(server_name)
        tool = next((item for item in server.tools if item.get("name") == tool_name), None)
        if tool is None or not tool.get("enabled", True):
            raise RuntimeError(f"MCP tool is not available: {server_name}/{tool_name}")
        if "mock_result" in tool:
            return tool["mock_result"]
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Called MCP tool {server_name}/{tool_name} with {arguments}",
                }
            ]
        }

    async def list_resources(self, server_name: str | None = None) -> list[dict[str, Any]]:
        servers = self._select_servers(server_name)
        return [
            {"server_name": server.name, **resource}
            for server in servers
            for resource in server.resources
        ]

    async def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        server = self._get_server(server_name)
        resource = next((item for item in server.resources if item.get("uri") == uri), None)
        if resource is None:
            raise RuntimeError(f"MCP resource is not available: {server_name}/{uri}")
        return resource

    async def list_prompts(self, server_name: str | None = None) -> list[dict[str, Any]]:
        servers = self._select_servers(server_name)
        return [
            {"server_name": server.name, **prompt}
            for server in servers
            for prompt in server.prompts
        ]

    async def get_prompt(self, server_name: str, name: str) -> dict[str, Any]:
        server = self._get_server(server_name)
        prompt = next((item for item in server.prompts if item.get("name") == name), None)
        if prompt is None:
            raise RuntimeError(f"MCP prompt is not available: {server_name}/{name}")
        return prompt

    def _select_servers(self, server_name: str | None) -> list[McpServerConfig]:
        if server_name is None:
            return list(self._servers.values())
        return [self._get_server(server_name)]

    def _get_server(self, server_name: str) -> McpServerConfig:
        server = self._servers.get(server_name)
        if server is None:
            raise RuntimeError(f"MCP server is not configured: {server_name}")
        return server

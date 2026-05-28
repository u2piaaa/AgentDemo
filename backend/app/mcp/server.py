from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mcp_security import McpIdentity, require_mcp_server_enabled
from app.mcp.prompts import LOCAL_PROMPTS
from app.mcp.resources import local_resource
from app.mcp.tools import local_tool_to_mcp_schema
from app.services.plugin_registry import PluginRegistry
from app.services.tool_executor import ToolExecutor


class AgentDemoMcpServer:
    def __init__(self, registry: PluginRegistry, executor: ToolExecutor | None = None) -> None:
        self.registry = registry
        self.executor = executor or ToolExecutor()

    async def list_tools(self) -> list[dict[str, Any]]:
        require_mcp_server_enabled()
        return [local_tool_to_mcp_schema(tool) for tool in self.registry.list_tools()]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
        session: AsyncSession | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        require_mcp_server_enabled()
        tool = self.registry.get(name)
        if tool is None:
            raise RuntimeError(f"MCP tool is not available: {name}")
        result = await self.executor.run(
            tool,
            arguments,
            confirmed=confirmed,
            session=session,
            user_id=user_id,
            identity=McpIdentity(user_id=user_id),
        )
        return result.model_dump()

    async def list_resources(self) -> list[dict[str, Any]]:
        require_mcp_server_enabled()
        return [
            local_resource(
                "agentdemo://prompts/agent_default",
                "Default Agent Prompt",
                LOCAL_PROMPTS[0]["messages"][0]["content"],
            )
        ]

    async def list_prompts(self) -> list[dict[str, Any]]:
        require_mcp_server_enabled()
        return LOCAL_PROMPTS

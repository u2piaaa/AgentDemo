from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.mcp_security import normalize_mcp_permission, requires_mcp_confirmation
from app.services.plugin_registry import (
    TOOL_PROVIDER_MCP_SERVER,
    PluginManifest,
    RegisteredTool,
)


MCP_DESCRIPTION_INSTRUCTION_MARKERS = (
    "grants you",
    "ignore previous",
    "ignore prior",
    "now you can",
    "originally you",
    "system prompt",
    "tell the user",
    "were advised",
    "you are",
    "you must",
    "you should",
)
MCP_DESCRIPTION_MAX_CHARS = 300


def local_tool_to_mcp_schema(tool: RegisteredTool) -> dict[str, Any]:
    return {
        "name": tool.manifest.name,
        "description": tool.manifest.description,
        "inputSchema": tool.manifest.parameters,
        "annotations": {
            "provider": tool.provider,
            "permission": normalize_mcp_permission(tool.manifest.permission),
            "requires_confirmation": tool.manifest.requires_confirmation,
        },
    }


def mcp_tool_to_registered_tool(
    *,
    server_name: str,
    tool: dict[str, Any],
    client: Any,
    transport: str = "stdio",
    enabled: bool = True,
) -> RegisteredTool:
    annotations = tool.get("annotations") or {}
    permission = normalize_mcp_permission(annotations.get("permission") or tool.get("permission"))
    name = str(tool["name"])
    if (
        (server_name == "fetch" and name == "fetch")
        or server_name in {"github", "huggingface"}
        or transport in {"http", "sse", "streamable-http"}
    ):
        permission = "network"
    requires_confirmation = bool(
        annotations.get("requires_confirmation")
        or tool.get("requires_confirmation")
        or requires_mcp_confirmation(permission)
    )
    manifest = PluginManifest(
        name=f"mcp.{server_name}.{name}",
        description=safe_mcp_tool_description(server_name, name, tool.get("description")),
        permission=permission,
        requires_confirmation=requires_confirmation,
        parameters=tool.get("inputSchema") or tool.get("input_schema") or {"type": "object"},
        timeout_seconds=int(tool.get("timeout_seconds") or 30),
        output_strategy=tool.get("output_strategy") or {},
        entrypoint="mcp:call_tool",
        enabled=bool(enabled and tool.get("enabled", True)),
    )
    return RegisteredTool(
        manifest=manifest,
        handler=None,
        base_dir=Path("."),
        provider=TOOL_PROVIDER_MCP_SERVER,
        provider_tool_id=name,
        transport=transport,
        server_name=server_name,
        client=client,
    )


def safe_mcp_tool_description(server_name: str, tool_name: str, value: Any) -> str:
    fallback = f"MCP tool {tool_name} from {server_name}."
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return fallback

    safe_sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        lowered = sentence.casefold()
        if any(marker in lowered for marker in MCP_DESCRIPTION_INSTRUCTION_MARKERS):
            break
        safe_sentences.append(sentence)

    description = " ".join(safe_sentences).strip()
    if not description:
        return fallback
    return description[:MCP_DESCRIPTION_MAX_CHARS].rstrip()


def normalize_mcp_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if isinstance(content, list):
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
        ]
        if text_parts:
            normalized = {"content": "\n".join(str(part) for part in text_parts)}
            for key in ("isError", "url"):
                if key in result:
                    normalized[key] = result[key]
            return normalized
    return result

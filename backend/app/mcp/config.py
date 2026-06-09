from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.mcp_security import contains_plaintext_mcp_secret


class McpServerConfig(BaseModel):
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    stdio_framing: str = "jsonl"
    enabled: bool = True
    tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)


class McpConfig(BaseModel):
    servers: list[McpServerConfig] = Field(default_factory=list)


def load_mcp_config(path: Path) -> McpConfig:
    if not path.exists():
        return McpConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if contains_plaintext_mcp_secret(raw):
        raise ValueError("MCP config contains plaintext secret fields")
    servers = raw.get("servers", [])
    if isinstance(servers, dict):
        servers = [{"name": name, **value} for name, value in servers.items()]
    servers = [_resolve_server_command(server, path.parent) for server in servers]
    return McpConfig(servers=servers)


def _resolve_server_command(server: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    command = server.get("command")
    if not isinstance(command, str):
        return server
    if "/" not in command and "\\" not in command:
        return server
    command_path = Path(command)
    if command_path.is_absolute():
        return server
    return {**server, "command": str((base_dir / command_path).resolve())}

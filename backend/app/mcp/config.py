from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.mcp_security import contains_plaintext_mcp_secret


class McpServerConfig(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    stdio_framing: Literal["jsonl", "content-length"] = "jsonl"
    protocol_mode: Literal["auto", "legacy", "2026-07-28"] = "auto"
    request_timeout_seconds: float = Field(default=30, gt=0, le=300)
    discovery_timeout_seconds: float = Field(default=15, gt=0, le=120)
    discovery_cache_seconds: float = Field(default=60, ge=0, le=3600)
    discovery_max_pages: int = Field(default=20, ge=1, le=100)
    discovery_max_items: int = Field(default=1000, ge=1, le=10000)
    max_response_bytes: int = Field(default=2_097_152, ge=1024, le=16_777_216)
    enabled: bool = True
    tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)


class McpConfig(BaseModel):
    servers: list[McpServerConfig] = Field(default_factory=list, max_length=64)
    discovery_concurrency: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def unique_names(self):
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        return self


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
    return McpConfig(servers=servers, discovery_concurrency=raw.get("discovery_concurrency", 4))


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

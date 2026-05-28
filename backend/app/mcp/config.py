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
    return McpConfig(servers=servers)

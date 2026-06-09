from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException

from app.core.config import Settings, get_settings

MCP_ACCESS_POLICIES = {"local-only", "authenticated", "disabled", "admin-only"}
MCP_PERMISSIONS = {"read", "write", "execute", "network", "destructive"}
RISKY_MCP_PERMISSIONS = {"write", "execute", "network", "destructive"}
SECRET_FIELD_MARKERS = ("key", "secret", "token", "password", "credential", "authorization")
BLOCKED_FETCH_HOSTS = {"localhost", "metadata.google.internal"}


@dataclass(frozen=True)
class McpIdentity:
    user_id: UUID | None = None
    service_id: str | None = None
    is_admin: bool = False

    @property
    def is_bound(self) -> bool:
        return self.user_id is not None or bool(self.service_id)


def validate_mcp_settings(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.mcp_access_policy not in MCP_ACCESS_POLICIES:
        raise RuntimeError(f"Invalid MCP access policy: {settings.mcp_access_policy}")
    unknown_transports = set(settings.mcp_allowed_transports) - {"stdio", "http", "sse", "streamable-http"}
    if unknown_transports:
        raise RuntimeError(f"Unsupported MCP transport(s): {sorted(unknown_transports)}")
    if settings.mcp_server_enabled and not settings.mcp_remote_enabled:
        _require_local_bind_host(settings.mcp_server_bind_host)


def require_mcp_server_enabled(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.mcp_enabled or not settings.mcp_server_enabled:
        raise HTTPException(status_code=404, detail="MCP server is disabled")


def require_mcp_client_enabled(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.mcp_enabled or not settings.mcp_client_enabled:
        raise HTTPException(status_code=404, detail="MCP client is disabled")


def authorize_mcp_access(
    identity: McpIdentity,
    *,
    settings: Settings | None = None,
    remote: bool = False,
) -> None:
    settings = settings or get_settings()
    policy = settings.mcp_access_policy
    if policy == "disabled":
        raise HTTPException(status_code=403, detail="MCP access is disabled")
    if remote and not settings.mcp_remote_enabled:
        raise HTTPException(status_code=403, detail="Remote MCP access is disabled")
    if policy == "local-only":
        if remote:
            raise HTTPException(status_code=403, detail="MCP access is local-only")
        return
    if policy == "authenticated" and not identity.is_bound:
        raise HTTPException(status_code=401, detail="MCP identity is required")
    if policy == "admin-only" and not identity.is_admin:
        raise HTTPException(status_code=403, detail="MCP admin access is required")


def normalize_mcp_permission(permission: str | None) -> str:
    if not permission:
        return "read"
    normalized = permission.strip().lower()
    if normalized == "safe":
        return "read"
    if normalized not in MCP_PERMISSIONS:
        return "execute"
    return normalized


def requires_mcp_confirmation(
    permission: str | None,
    *,
    explicit_requires_confirmation: bool = False,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    normalized = normalize_mcp_permission(permission)
    if explicit_requires_confirmation:
        return True
    if normalized in RISKY_MCP_PERMISSIONS:
        return True
    return bool(settings.mcp_require_confirmation_by_default and normalized != "read")


def enforce_mcp_tool_policy(
    *,
    permission: str | None,
    requires_confirmation: bool,
    confirmed: bool,
    identity: McpIdentity,
    settings: Settings | None = None,
    remote: bool = False,
) -> None:
    authorize_mcp_access(identity, settings=settings, remote=remote)
    if requires_mcp_confirmation(permission, explicit_requires_confirmation=requires_confirmation) and not confirmed:
        raise HTTPException(status_code=409, detail="MCP tool requires confirmation before execution")


def assert_workspace_relative_path(path: str, workspace: Path) -> Path:
    if "\x00" in path:
        raise HTTPException(status_code=422, detail="Path contains invalid characters")
    root = workspace.resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=403, detail="MCP path is outside the workspace")
    return resolved


def validate_mcp_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="Fetch URL must use http or https")
    if not parsed.hostname:
        raise HTTPException(status_code=422, detail="Fetch URL must include a host")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in BLOCKED_FETCH_HOSTS or hostname.endswith(".localhost"):
        raise HTTPException(status_code=403, detail="Fetch URL targets a blocked local host")

    try:
        address = ip_address(hostname)
    except ValueError:
        return

    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise HTTPException(status_code=403, detail="Fetch URL targets a blocked local or private address")


def scrub_mcp_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _looks_secret_key(key) else scrub_mcp_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_mcp_config(item) for item in value]
    return value


def contains_plaintext_mcp_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _looks_secret_key(key) and not _is_safe_secret_reference(item):
                return True
            if contains_plaintext_mcp_secret(item):
                return True
    if isinstance(value, list):
        return any(contains_plaintext_mcp_secret(item) for item in value)
    return False


def _require_local_bind_host(host: str) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MCP server must bind locally unless MCP remote access is enabled")


def _looks_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def _is_safe_secret_reference(value: Any) -> bool:
    if value in (None, "", "***REDACTED***"):
        return True
    if not isinstance(value, str):
        return False
    return bool(
        re.fullmatch(
            r"(?:Bearer\s+)?\$\{[A-Za-z_][A-Za-z0-9_]*\}",
            value.strip(),
            flags=re.IGNORECASE,
        )
    )

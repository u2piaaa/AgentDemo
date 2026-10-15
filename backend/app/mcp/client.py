from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json
import os
import re
from time import monotonic
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.legacy_stdio import LegacyStdioConnection


class LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                raise RuntimeError("MCP HTTP response exceeded its byte limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class BoundedHttpClient(httpx2.AsyncClient):
    """Cap JSON as well as SSE bodies before the SDK parses them."""

    def __init__(self, *, max_response_bytes, **kwargs):
        super().__init__(**kwargs)
        self.max_response_bytes = max_response_bytes
        self.last_error_status = None

    async def send(self, request, *, stream=False, **kwargs):
        response = await super().send(request, stream=True, **kwargs)
        if response.status_code in {401, 403} or response.status_code >= 500:
            self.last_error_status = response.status_code
        if response.is_stream_consumed:
            if len(response.content) > self.max_response_bytes:
                await response.aclose()
                raise RuntimeError("MCP HTTP response exceeded its byte limit")
        else:
            response.stream = LimitedStream(response.stream, self.max_response_bytes)
        if not stream:
            try:
                await response.aread()
            finally:
                await response.aclose()
        return response


class McpClientManager:
    """SDK-backed MCP with bounded discovery and truthful last-contact state.

    Connections are owned and closed in the calling task. No SDK task group is
    entered by one request and exited by another. Only catalog metadata is cached;
    tool executions are never cached or retried by this manager.
    """

    def __init__(
        self, config: McpConfig | None = None, *, http_transport_factory: Callable | None = None
    ):
        self.config = config or McpConfig()
        self._servers = {server.name: server for server in self.config.servers}
        self._http_transport_factory = http_transport_factory
        self._catalogs: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._discovery_slots = asyncio.Semaphore(self.config.discovery_concurrency)
        self._state = {
            server.name: {
                "status": "configured" if server.enabled else "disabled",
                "protocol_version": None,
                "last_checked_at": None,
                "error": None,
                "capabilities": {},
                **{
                    f"{kind[:-1]}_count": len(getattr(server, kind))
                    for kind in ("tools", "resources", "prompts")
                },
            }
            for server in self.config.servers
        }

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": server.name,
                "transport": server.transport,
                **deepcopy(self._state[server.name]),
            }
            for server in self.config.servers
        ]

    async def list_tools(self, server_name: str | None = None) -> list[dict]:
        return await self._list("tools", server_name)

    async def list_resources(self, server_name: str | None = None) -> list[dict]:
        return await self._list("resources", server_name)

    async def list_prompts(self, server_name: str | None = None) -> list[dict]:
        return await self._list("prompts", server_name)

    async def _list(self, kind, server_name):
        if server_name is not None:
            return await self._catalog(self._get_server(server_name), kind)
        servers = [server for server in self.config.servers if server.enabled]
        results = await asyncio.gather(
            *(self._catalog(server, kind) for server in servers), return_exceptions=True
        )
        return [item for result in results if isinstance(result, list) for item in result]

    async def _catalog(self, server, kind):
        key = (server.name, kind)
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._catalogs.get(key)
            if cached and cached[0] > monotonic():
                return deepcopy(cached[1])
            configured = getattr(server, kind)
            try:
                if configured:
                    values = deepcopy(configured)
                    if not self._has_transport(server) and any(
                        "mock_result" in item for item in configured
                    ):
                        self._state[server.name]["status"] = "mock"
                elif not self._has_transport(server):
                    values = []
                else:
                    async with self._discovery_slots:
                        with anyio.fail_after(server.discovery_timeout_seconds):
                            async with self._connection(server) as client:
                                values = await self._pages(client, server, kind)
                # Server-provided fields must not override the configured routing identity.
                values = [
                    {**item, "server_name": server.name, "transport": server.transport}
                    for item in values
                    if item.get("enabled", True)
                ]
                self._state[server.name][f"{kind[:-1]}_count"] = len(values)
                self._catalogs[key] = (monotonic() + server.discovery_cache_seconds, values)
                return deepcopy(values)
            except Exception as exc:
                self._record_error(server, exc)
                raise RuntimeError(self._state[server.name]["error"]) from exc

    async def _pages(self, client, server, kind):
        values, seen_cursors, seen_names = [], set(), set()
        cursor = None
        capabilities = self._state[server.name]["capabilities"]
        if kind not in capabilities:
            return []
        for _ in range(server.discovery_max_pages):
            try:
                page = await self._request(
                    client, f"{kind}/list", {"cursor": cursor} if cursor else {}
                )
            except MCPError as exc:
                if exc.code == -32601 and kind in {"resources", "prompts"}:
                    return []
                raise
            if len(json.dumps(page, ensure_ascii=False).encode()) > server.max_response_bytes:
                raise RuntimeError("MCP catalog response exceeded its byte limit")
            batch = page.get(kind)
            if not isinstance(batch, list):
                raise RuntimeError("MCP catalog response is missing its item list")
            for item in batch:
                identity = (
                    item.get("uri" if kind == "resources" else "name")
                    if isinstance(item, dict)
                    else None
                )
                if not identity or identity in seen_names:
                    raise RuntimeError("MCP catalog contains missing or duplicate identifiers")
                seen_names.add(identity)
                values.append(item)
                if len(values) > server.discovery_max_items:
                    raise RuntimeError("MCP catalog exceeded its item limit")
            cursor = page.get("nextCursor")
            if not cursor:
                return values
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise RuntimeError("MCP catalog repeated an invalid pagination cursor")
            seen_cursors.add(cursor)
        raise RuntimeError("MCP catalog exceeded its page limit")

    async def call_tool(self, server_name, tool_name, arguments):
        server = self._get_server(server_name)
        tool = next((item for item in server.tools if item.get("name") == tool_name), None)
        if tool is not None:
            if not tool.get("enabled", True):
                raise RuntimeError(f"MCP tool is disabled: {server_name}/{tool_name}")
            if "mock_result" in tool:
                self._state[server.name]["status"] = "mock"
                return deepcopy(tool["mock_result"])
        return await self._run(server, "tools/call", {"name": tool_name, "arguments": arguments})

    async def read_resource(self, server_name, uri):
        server = self._get_server(server_name)
        resource = next((item for item in server.resources if item.get("uri") == uri), None)
        if resource is not None and (
            "content" in resource or "contents" in resource or not self._has_transport(server)
        ):
            return deepcopy(resource)
        return await self._run(server, "resources/read", {"uri": uri})

    async def get_prompt(self, server_name, name, arguments=None):
        server = self._get_server(server_name)
        prompt = next((item for item in server.prompts if item.get("name") == name), None)
        if prompt is not None and ("messages" in prompt or not self._has_transport(server)):
            return deepcopy(prompt)
        return await self._run(server, "prompts/get", {"name": name, "arguments": arguments or {}})

    async def _run(self, server, method, params):
        try:
            if not self._has_transport(server):
                raise RuntimeError("No MCP transport or explicit mock result is configured")
            with anyio.fail_after(server.request_timeout_seconds):
                async with self._connection(server) as client:
                    result = await self._request(client, method, params)
                    if (
                        len(json.dumps(result, ensure_ascii=False).encode())
                        > server.max_response_bytes
                    ):
                        raise RuntimeError("MCP result exceeded its byte limit")
                    return result
        except Exception as exc:
            self._record_error(server, exc)
            raise RuntimeError(self._state[server.name]["error"]) from exc

    @asynccontextmanager
    async def _connection(self, server):
        if server.transport == "stdio" and server.stdio_framing == "content-length":
            async with LegacyStdioConnection(server, self._environment(server)) as client:
                self._record_connection(server, client.protocol_version, client.capabilities)
                yield client
            return
        if server.transport == "stdio":
            transport = StdioServerParameters(
                command=server.command, args=server.args, env=self._environment(server)
            )
            async with self._sdk_client(transport, server) as client:
                yield client
        elif server.transport == "sse":
            transport = sse_client(
                server.url,
                headers=self._http_headers(server),
                timeout=server.request_timeout_seconds,
                sse_read_timeout=server.request_timeout_seconds,
                httpx_client_factory=lambda **kwargs: self._http_client(server, **kwargs),
            )
            async with self._sdk_client(transport, server, mode="legacy") as client:
                yield client
        else:
            async with self._http_client(
                server,
                headers=self._http_headers(server),
                timeout=httpx2.Timeout(server.request_timeout_seconds, connect=10),
            ) as http:
                transport = streamable_http_client(
                    server.url, http_client=http, max_sse_event_size=server.max_response_bytes
                )
                try:
                    async with self._sdk_client(transport, server) as client:
                        yield client
                except Exception:
                    if http.last_error_status:
                        raise RuntimeError(
                            f"HTTP {http.last_error_status}; check server access and configured credentials"
                        ) from None
                    raise

    @asynccontextmanager
    async def _sdk_client(self, transport, server: McpServerConfig, mode=None):
        requested_mode = mode or server.protocol_mode
        async with Client(
            transport,
            mode="auto" if requested_mode == "2026-07-28" else requested_mode,
            read_timeout_seconds=server.request_timeout_seconds,
            input_required_max_rounds=0,
        ) as client:
            if requested_mode == "2026-07-28" and client.protocol_version != requested_mode:
                raise RuntimeError("MCP server does not support the configured protocol version")
            capabilities = client.server_capabilities.model_dump(by_alias=True, exclude_none=True)
            self._record_connection(server, client.protocol_version, capabilities)
            yield client

    async def _request(self, client, method, params):
        if isinstance(client, LegacyStdioConnection):
            return await client.request(method, params)
        if method in {"tools/list", "resources/list", "prompts/list"}:
            result = await getattr(client, "list_" + method.split("/")[0])(
                cursor=params.get("cursor")
            )
        elif method == "tools/call":
            result = await client.call_tool(params["name"], params["arguments"])
        elif method == "resources/read":
            result = await client.read_resource(params["uri"])
        else:
            result = await client.get_prompt(params["name"], params["arguments"])
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _record_connection(self, server, version, capabilities):
        self._state[server.name].update(
            status="ready",
            protocol_version=version,
            capabilities=capabilities,
            error=None,
            last_checked_at=datetime.now(UTC).isoformat(),
        )

    def _record_error(self, server, error):
        while isinstance(error, BaseExceptionGroup) and error.exceptions:
            error = error.exceptions[0]
        if isinstance(error, httpx2.HTTPStatusError):
            detail = (
                f"HTTP {error.response.status_code}; check server access and configured credentials"
            )
        elif isinstance(error, (TimeoutError, httpx2.TimeoutException)):
            detail = "operation timed out"
        elif isinstance(error, MCPError):
            detail = f"JSON-RPC error {error.code}"
        elif isinstance(error, ValidationError):
            detail = "server response failed protocol validation"
        elif isinstance(error, (OSError, httpx2.HTTPError)):
            detail = f"transport unavailable ({type(error).__name__})"
        else:
            message = str(error)
            safe_prefixes = (
                "MCP ",
                "No MCP ",
                "Missing MCP ",
                "HTTP ",
                "Unsupported legacy MCP",
                "Invalid or oversized MCP",
                "Invalid MCP",
            )
            # SDK exceptions can embed complete tool results or validation inputs.
            detail = (
                message[:240]
                if message.startswith(safe_prefixes)
                else f"protocol operation failed ({type(error).__name__})"
            )
        # SDK validation and transport diagnostics can contain remote payloads/URLs.
        for value in (*server.headers.values(), *server.env.values()):
            for variable in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value):
                secret = os.environ.get(variable)
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            try:
                expanded = self._expand(value)
            except RuntimeError:
                continue
            if expanded:
                detail = detail.replace(expanded, "[redacted]")
        detail = re.sub(r"https?://\S+", "[endpoint]", detail)
        self._state[server.name].update(
            status="error",
            error=f"MCP server {server.name}: {detail}",
            last_checked_at=datetime.now(UTC).isoformat(),
        )

    def _get_server(self, name):
        server = self._servers.get(name)
        if server is None or not server.enabled:
            raise RuntimeError(f"MCP server is not enabled: {name}")
        return server

    def invalidate_catalogs(self):
        self._catalogs.clear()

    @staticmethod
    def _has_transport(server):
        return bool(server.command) if server.transport == "stdio" else bool(server.url)

    def _http_client(self, server, **kwargs):
        if self._http_transport_factory:
            kwargs["transport"] = self._http_transport_factory()
        return BoundedHttpClient(
            max_response_bytes=server.max_response_bytes, follow_redirects=False, **kwargs
        )

    def _http_headers(self, server):
        return {key: self._expand(value) for key, value in server.headers.items()}

    def _environment(self, server):
        # The SDK inherits only OS essentials; provider credentials require explicit mapping.
        return {key: self._expand(value) for key, value in server.env.items()}

    @staticmethod
    def _expand(value):
        def replace(match):
            value = os.environ.get(match.group(1))
            if not value:
                raise RuntimeError(f"Missing MCP environment variable: {match.group(1)}")
            return value

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)

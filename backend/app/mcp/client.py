from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx

from app.mcp.config import McpConfig, McpServerConfig


MCP_PROTOCOL_VERSION = "2025-06-18"


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
        tools: list[dict[str, Any]] = []
        for server in servers:
            try:
                server_tools = server.tools
                if not server_tools and self._can_call_stdio(server):
                    server_tools = await self._stdio_result_list(server, "tools/list", "tools")
                if not server_tools and self._can_call_http(server):
                    server_tools = await self._http_result_list(server, "tools/list", "tools")
            except Exception:
                if server_name is not None:
                    raise
                continue
            tools.extend(
                {
                    "server_name": server.name,
                    "transport": server.transport,
                    **tool,
                }
                for tool in server_tools
                if tool.get("enabled", True)
            )
        return tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        server = self._get_server(server_name)
        tool = next((item for item in server.tools if item.get("name") == tool_name), None)
        if tool is not None and not tool.get("enabled", True):
            raise RuntimeError(f"MCP tool is not available: {server_name}/{tool_name}")
        if tool is not None and "mock_result" in tool:
            return tool["mock_result"]
        if self._can_call_stdio(server):
            return await self._stdio_request(
                server,
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        if self._can_call_http(server):
            return await self._http_request(
                server,
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        if tool is None:
            raise RuntimeError(f"MCP tool is not available: {server_name}/{tool_name}")
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
        resources: list[dict[str, Any]] = []
        for server in servers:
            try:
                server_resources = server.resources
                if not server_resources and self._can_call_stdio(server):
                    try:
                        server_resources = await self._stdio_result_list(
                            server, "resources/list", "resources"
                        )
                    except RuntimeError as exc:
                        if not self._is_method_not_found(exc):
                            raise
                        server_resources = []
                if not server_resources and self._can_call_http(server):
                    try:
                        server_resources = await self._http_result_list(
                            server, "resources/list", "resources"
                        )
                    except RuntimeError as exc:
                        if not self._is_method_not_found(exc):
                            raise
                        server_resources = []
            except Exception:
                if server_name is not None:
                    raise
                continue
            resources.extend(
                {"server_name": server.name, "transport": server.transport, **resource}
                for resource in server_resources
            )
        return resources

    async def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        server = self._get_server(server_name)
        resource = next((item for item in server.resources if item.get("uri") == uri), None)
        if resource is None and self._can_call_stdio(server):
            return await self._stdio_request(server, "resources/read", {"uri": uri})
        if resource is None and self._can_call_http(server):
            return await self._http_request(server, "resources/read", {"uri": uri})
        if resource is None:
            raise RuntimeError(f"MCP resource is not available: {server_name}/{uri}")
        return resource

    async def list_prompts(self, server_name: str | None = None) -> list[dict[str, Any]]:
        servers = self._select_servers(server_name)
        prompts: list[dict[str, Any]] = []
        for server in servers:
            try:
                server_prompts = server.prompts
                if not server_prompts and self._can_call_stdio(server):
                    try:
                        server_prompts = await self._stdio_result_list(
                            server, "prompts/list", "prompts"
                        )
                    except RuntimeError as exc:
                        if not self._is_method_not_found(exc):
                            raise
                        server_prompts = []
                if not server_prompts and self._can_call_http(server):
                    try:
                        server_prompts = await self._http_result_list(
                            server, "prompts/list", "prompts"
                        )
                    except RuntimeError as exc:
                        if not self._is_method_not_found(exc):
                            raise
                        server_prompts = []
            except Exception:
                if server_name is not None:
                    raise
                continue
            prompts.extend(
                {"server_name": server.name, "transport": server.transport, **prompt}
                for prompt in server_prompts
            )
        return prompts

    async def get_prompt(self, server_name: str, name: str) -> dict[str, Any]:
        server = self._get_server(server_name)
        prompt = next((item for item in server.prompts if item.get("name") == name), None)
        if prompt is None and self._can_call_stdio(server):
            return await self._stdio_request(server, "prompts/get", {"name": name})
        if prompt is None and self._can_call_http(server):
            return await self._http_request(server, "prompts/get", {"name": name})
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

    def _can_call_stdio(self, server: McpServerConfig) -> bool:
        return server.transport == "stdio" and bool(server.command)

    def _can_call_http(self, server: McpServerConfig) -> bool:
        return server.transport in {"http", "sse", "streamable-http"} and bool(server.url)

    async def _stdio_result_list(
        self,
        server: McpServerConfig,
        method: str,
        result_key: str,
    ) -> list[dict[str, Any]]:
        result = await self._stdio_request(server, method, {})
        values = result.get(result_key, []) if isinstance(result, dict) else []
        return [item for item in values if isinstance(item, dict)]

    async def _http_result_list(
        self,
        server: McpServerConfig,
        method: str,
        result_key: str,
    ) -> list[dict[str, Any]]:
        result = await self._http_request(server, method, {})
        values = result.get(result_key, []) if isinstance(result, dict) else []
        return [item for item in values if isinstance(item, dict)]

    async def _http_request(
        self,
        server: McpServerConfig,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        headers = self._http_headers(server)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            initialize_response = await self._http_json_rpc(
                client,
                server,
                headers,
                1,
                "initialize",
                self._initialize_params(server),
            )
            session_id = initialize_response.headers.get(
                "Mcp-Session-Id"
            ) or initialize_response.headers.get("mcp-session-id")
            if session_id:
                headers = {**headers, "Mcp-Session-Id": session_id}
            await self._http_json_rpc(
                client,
                server,
                headers,
                None,
                "notifications/initialized",
                {},
            )
            response = await self._http_json_rpc(client, server, headers, 2, method, params)
            message = self._parse_http_rpc_message(response, 2, server)
            if "error" in message:
                raise RuntimeError(f"MCP server {server.name} returned error: {message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP server {server.name} returned a non-object result")
            return result

    async def _http_json_rpc(
        self,
        client: httpx.AsyncClient,
        server: McpServerConfig,
        headers: dict[str, str],
        request_id: int | None,
        method: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        try:
            response = await client.post(str(server.url), headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"MCP HTTP server {server.name} timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"MCP HTTP server {server.name} could not be reached: {exc.__class__.__name__}"
            ) from exc

        if response.status_code in {401, 403}:
            raise RuntimeError(
                f"MCP HTTP server {server.name} returned {response.status_code}; "
                "check the configured Hugging Face token or OAuth settings"
            )
        if response.status_code >= 400:
            detail = response.text[:300].strip()
            raise RuntimeError(
                f"MCP HTTP server {server.name} returned HTTP {response.status_code}: {detail}"
            )
        if request_id is None:
            return response
        message = self._parse_http_rpc_message(response, request_id, server)
        if "error" in message:
            raise RuntimeError(f"MCP server {server.name} returned error: {message['error']}")
        return response

    def _parse_http_rpc_message(
        self,
        response: httpx.Response,
        request_id: int,
        server: McpServerConfig,
    ) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            messages = self._parse_sse_messages(response.text)
        else:
            try:
                parsed = response.json()
            except ValueError as exc:
                raise RuntimeError(f"MCP HTTP server {server.name} returned invalid JSON") from exc
            messages = parsed if isinstance(parsed, list) else [parsed]
        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise RuntimeError(f"MCP HTTP server {server.name} response missing id {request_id}")

    def _parse_sse_messages(self, text: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        data_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
                continue
            if line.strip():
                continue
            if data_lines:
                messages.append(json.loads("\n".join(data_lines)))
                data_lines = []
        if data_lines:
            messages.append(json.loads("\n".join(data_lines)))
        return messages

    async def _stdio_request(
        self,
        server: McpServerConfig,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            str(server.command),
            *[str(arg) for arg in server.args],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._process_env(server),
        )
        try:
            await self._send_request(process, server, 1, "initialize", self._initialize_params(server))
            await self._read_response(process, 1, server)
            await self._send_notification(process, server, "notifications/initialized", {})
            await self._send_request(process, server, 2, method, params)
            return await self._read_response(process, 2, server)
        finally:
            await self._close_process(process)

    def _initialize_params(self, server: McpServerConfig) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "AgentDemo", "version": "0.1.0"},
            "serverName": server.name,
        }

    def _process_env(self, server: McpServerConfig) -> dict[str, str]:
        env = os.environ.copy()
        for key, value in server.env.items():
            env[key] = self._expand_env_placeholders(value)
        return env

    def _http_headers(self, server: McpServerConfig) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        headers.update(
            {key: self._expand_env_placeholders(value) for key, value in server.headers.items()}
        )
        return headers

    def _expand_env_placeholders(self, value: str) -> str:
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: os.environ.get(match.group(1), ""),
            value,
        )

    async def _send_request(
        self,
        process: asyncio.subprocess.Process,
        server: McpServerConfig,
        request_id: int,
        method: str,
        params: dict[str, Any],
    ) -> None:
        await self._write_message(
            process,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            server,
        )

    async def _send_notification(
        self,
        process: asyncio.subprocess.Process,
        server: McpServerConfig,
        method: str,
        params: dict[str, Any],
    ) -> None:
        await self._write_message(
            process,
            {"jsonrpc": "2.0", "method": method, "params": params},
            server,
        )

    async def _write_message(
        self,
        process: asyncio.subprocess.Process,
        payload: dict[str, Any],
        server: McpServerConfig,
    ) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP stdio process has no stdin")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if server.stdio_framing == "content-length":
            process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        else:
            process.stdin.write(body + b"\n")
        await process.stdin.drain()

    async def _read_response(
        self,
        process: asyncio.subprocess.Process,
        request_id: int,
        server: McpServerConfig,
    ) -> dict[str, Any]:
        while True:
            message = await asyncio.wait_for(self._read_message(process), timeout=30)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP server {server.name} returned error: {message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP server {server.name} returned a non-object result")
            return result

    async def _read_message(self, process: asyncio.subprocess.Process) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("MCP stdio process has no stdout")
        content_length: int | None = None
        while True:
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("MCP stdio process ended before returning a response")
            stripped = line.strip()
            if not stripped:
                break
            if stripped.lower().startswith(b"content-length:"):
                content_length = int(stripped.split(b":", 1)[1].strip())
            elif stripped.startswith(b"{"):
                return json.loads(stripped.decode("utf-8"))
        if content_length is None:
            raise RuntimeError("MCP response missing Content-Length header")
        body = await process.stdout.readexactly(content_length)
        return json.loads(body.decode("utf-8"))

    async def _close_process(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.terminate()
            await process.wait()

    def _is_method_not_found(self, exc: RuntimeError) -> bool:
        message = str(exc)
        return "-32601" in message or "Method not found" in message

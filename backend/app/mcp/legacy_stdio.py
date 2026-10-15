"""Compatibility for old nonstandard Content-Length subprocess configurations.

Standard JSONL and HTTP transports use the official SDK. This adapter retains
existing installations while bounding frames, stderr handling and shutdown.
"""

import asyncio
from contextlib import suppress
import json
import subprocess

import anyio
from mcp.client.stdio import get_default_environment
from mcp.shared.exceptions import MCPError


class LegacyStdioConnection:
    def __init__(self, server, environment):
        self.server = server
        self.environment = environment
        self.process = None
        self.counter = 0
        self.protocol_version = None
        self.capabilities = {}

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            self.server.command,
            *self.server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=get_default_environment() | self.environment,
            limit=self.server.max_response_bytes,
        )
        try:
            initialized = await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "AgentDemo", "version": "0.1.0"},
                },
            )
            self.protocol_version = initialized.get("protocolVersion")
            if self.protocol_version not in {
                "2024-11-05",
                "2025-03-26",
                "2025-06-18",
                "2025-11-25",
            }:
                raise RuntimeError("Unsupported legacy MCP protocol version")
            self.capabilities = initialized.get("capabilities", {})
            await self._write(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        if self.process is None:
            return
        with anyio.move_on_after(5, shield=True):
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=1)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()

    async def _write(self, message):
        body = json.dumps(message, separators=(",", ":")).encode()
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await self.process.stdin.drain()

    async def request(self, method, params):
        self.counter += 1
        request_id = self.counter
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            length = None
            for _ in range(16):
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("MCP subprocess ended before returning a response")
                if not line.strip():
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            else:
                raise RuntimeError("Invalid MCP frame: too many headers")
            if length is None or not 0 < length <= self.server.max_response_bytes:
                raise RuntimeError("Invalid or oversized MCP Content-Length frame")
            message = json.loads(await self.process.stdout.readexactly(length))
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise RuntimeError("Invalid MCP JSON-RPC response")
            if "method" in message:
                if "id" in message:
                    await self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Client capability not supported"},
                        }
                    )
                continue
            if message.get("id") != request_id:
                raise RuntimeError("MCP response does not match its request")
            if "error" in message:
                error = message["error"]
                raise MCPError(error["code"], error.get("message", "MCP error"))
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("MCP returned a non-object result")
            return result

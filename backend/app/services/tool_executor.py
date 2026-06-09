import asyncio
import json
from collections.abc import Mapping
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.mcp_security import McpIdentity, enforce_mcp_tool_policy, validate_mcp_fetch_url
from app.mcp.tools import normalize_mcp_tool_result
from app.models.task import Task
from app.models.tool import ToolCall
from app.schemas import ToolRunResponse
from app.services.plugin_registry import TOOL_PROVIDER_MCP_SERVER, RegisteredTool


class ToolExecutor:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
        session: AsyncSession | None = None,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        identity: McpIdentity | None = None,
    ) -> ToolRunResponse:
        trace_id = uuid4().hex
        started = perf_counter()
        if not tool.manifest.enabled:
            response = self._response(tool, "failed", started, trace_id, error="Tool is disabled")
            await self._record_audit(
                session, tool, arguments, response, user_id, conversation_id, task_id
            )
            return response
        if tool.manifest.requires_confirmation and not confirmed:
            response = self._response(
                tool,
                "failed",
                started,
                trace_id,
                error="Tool requires confirmation before execution",
            )
            await self._record_audit(
                session, tool, arguments, response, user_id, conversation_id, task_id
            )
            return response

        if tool.provider == TOOL_PROVIDER_MCP_SERVER:
            await self._record_mcp_task_event(
                session,
                task_id,
                "mcp_tool_call_started",
                tool,
                {"trace_id": trace_id},
                progress=25,
            )

        try:
            self._validate_arguments(tool, arguments)
            timeout = min(tool.manifest.timeout_seconds, self.settings.tool_timeout_seconds)
            output = await asyncio.wait_for(
                self._dispatch_tool(tool, arguments, confirmed=confirmed, identity=identity),
                timeout=timeout,
            )
        except TimeoutError:
            response = self._response(
                tool,
                "timeout",
                started,
                trace_id,
                error="Tool execution timed out",
            )
            await self._record_audit(
                session, tool, arguments, response, user_id, conversation_id, task_id
            )
            return response
        except HTTPException as exc:
            response = self._response(tool, "failed", started, trace_id, error=str(exc.detail))
            await self._record_audit(
                session, tool, arguments, response, user_id, conversation_id, task_id
            )
            return response
        except Exception as exc:
            response = self._response(tool, "failed", started, trace_id, error=str(exc))
            await self._record_audit(
                session, tool, arguments, response, user_id, conversation_id, task_id
            )
            return response

        limited_output = self._limit_output(tool, output)
        response = self._response(tool, "success", started, trace_id, output=limited_output)
        await self._record_audit(session, tool, arguments, response, user_id, conversation_id, task_id)
        return response

    def _response(
        self,
        tool: RegisteredTool,
        status: str,
        started: float,
        trace_id: str,
        *,
        output: Any = None,
        error: str | None = None,
    ) -> ToolRunResponse:
        return ToolRunResponse(
            tool_name=tool.manifest.name,
            provider=tool.provider,
            provider_tool_id=tool.provider_tool_id or tool.manifest.name,
            server_name=tool.server_name,
            status=status,
            output=output,
            output_summary=self._summarize(output),
            error=error,
            duration_ms=int((perf_counter() - started) * 1000),
            trace_id=trace_id,
        )

    def _validate_arguments(self, tool: RegisteredTool, arguments: dict[str, Any]) -> None:
        schema = tool.manifest.parameters
        if schema.get("type", "object") != "object":
            raise HTTPException(status_code=500, detail="Tool parameter schema must be an object")

        if not isinstance(arguments, Mapping):
            raise HTTPException(status_code=422, detail="Tool arguments must be an object")

        required = schema.get("required", [])
        missing = [name for name in required if name not in arguments]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing required arguments: {missing}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(name for name in arguments if name not in properties)
            if unknown:
                raise HTTPException(status_code=422, detail=f"Unknown arguments: {unknown}")

        errors: list[str] = []
        for name, value in arguments.items():
            property_schema = properties.get(name)
            if not isinstance(property_schema, Mapping):
                continue
            expected_type = property_schema.get("type")
            if expected_type is not None and not self._matches_schema_type(value, expected_type):
                errors.append(f"{name} must be {self._type_label(expected_type)}")
                continue
            fmt = property_schema.get("format")
            if fmt is not None and not self._matches_format(value, fmt):
                errors.append(f"{name} must match format {fmt}")

        if errors:
            raise HTTPException(status_code=422, detail="; ".join(errors))

    def _matches_schema_type(self, value: Any, expected_type: Any) -> bool:
        if isinstance(expected_type, list):
            return any(self._matches_schema_type(value, item) for item in expected_type)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, Mapping)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "null":
            return value is None
        return True

    def _type_label(self, expected_type: Any) -> str:
        if isinstance(expected_type, list):
            return " or ".join(str(item) for item in expected_type)
        return str(expected_type)

    def _matches_format(self, value: Any, fmt: str) -> bool:
        if not isinstance(value, str):
            return True
        if fmt == "path":
            return bool(value.strip()) and "\x00" not in value
        if fmt == "uuid":
            from uuid import UUID

            try:
                UUID(value)
            except ValueError:
                return False
            return True
        return True

    def _limit_output(self, tool: RegisteredTool, output: Any) -> Any:
        max_chars = self._output_limit(tool)
        if isinstance(output, str):
            return output[:max_chars]
        if isinstance(output, dict):
            return {
                key: value[:max_chars] if isinstance(value, str) else value
                for key, value in output.items()
            }
        return output

    def _output_limit(self, tool: RegisteredTool) -> int:
        policy_limit = tool.manifest.output_strategy.get("max_chars")
        if isinstance(policy_limit, int) and policy_limit > 0:
            return min(policy_limit, self.settings.max_tool_output_chars)
        return self.settings.max_tool_output_chars

    def _summarize(self, output: Any) -> str | None:
        if output is None:
            return None
        if isinstance(output, str):
            text = output
        else:
            try:
                text = json.dumps(output, ensure_ascii=False, default=str)
            except TypeError:
                text = str(output)
        max_chars = min(self.settings.max_tool_output_chars, 500)
        return text[:max_chars]

    async def _record_audit(
        self,
        session: AsyncSession | None,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        response: ToolRunResponse,
        user_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
    ) -> None:
        if session is None:
            return
        session.add(
            ToolCall(
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
                tool_name=tool.manifest.name,
                provider=tool.provider,
                status=response.status,
                input=arguments,
                input_summary=self._summarize(arguments),
                output_summary=response.output_summary,
                error=response.error,
                duration_ms=response.duration_ms,
                trace_id=response.trace_id,
            )
        )
        await self._record_mcp_task_event(
            session,
            task_id,
            "mcp_tool_call_finished" if response.status == "success" else "mcp_tool_call_failed",
            tool,
            {
                "trace_id": response.trace_id,
                "status": response.status,
                "error": response.error,
                "duration_ms": response.duration_ms,
            },
            progress=100 if response.status == "success" else None,
            commit=False,
        )
        await session.commit()

    async def _dispatch_tool(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        *,
        confirmed: bool,
        identity: McpIdentity | None,
    ) -> Any:
        if tool.provider == TOOL_PROVIDER_MCP_SERVER:
            if tool.client is None or tool.server_name is None or tool.provider_tool_id is None:
                raise RuntimeError("MCP tool is missing client metadata")
            if tool.server_name == "fetch" and tool.provider_tool_id == "fetch":
                validate_mcp_fetch_url(str(arguments.get("url") or ""))
            enforce_mcp_tool_policy(
                permission=tool.manifest.permission,
                requires_confirmation=tool.manifest.requires_confirmation,
                confirmed=confirmed,
                identity=identity or McpIdentity(),
            )
            result = await tool.client.call_tool(tool.server_name, tool.provider_tool_id, arguments)
            return normalize_mcp_tool_result(result)
        if tool.handler is None:
            raise RuntimeError("Local tool is missing a handler")
        return await asyncio.to_thread(tool.handler, **arguments)

    async def _record_mcp_task_event(
        self,
        session: AsyncSession | None,
        task_id: UUID | None,
        event_type: str,
        tool: RegisteredTool,
        payload: dict[str, Any],
        *,
        progress: int | None = None,
        commit: bool = True,
    ) -> None:
        if session is None or task_id is None or tool.provider != TOOL_PROVIDER_MCP_SERVER:
            return
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return
        event = {
            "type": event_type,
            "tool_name": tool.manifest.name,
            "provider": tool.provider,
            "server_name": tool.server_name,
            "provider_tool_id": tool.provider_tool_id,
            **payload,
        }
        metadata = dict(task.metadata_ or {})
        metadata["events"] = [*(metadata.get("events") or []), event]
        task.metadata_ = metadata
        if progress is not None:
            task.progress = max(task.progress or 0, progress)
        if commit:
            await session.commit()

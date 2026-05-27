import asyncio
import json
from collections.abc import Mapping
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import get_settings
from app.schemas import ToolRunResponse
from app.services.plugin_registry import RegisteredTool


class ToolExecutor:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> ToolRunResponse:
        trace_id = uuid4().hex
        started = perf_counter()
        if not tool.manifest.enabled:
            return self._response(tool, "failed", started, trace_id, error="Tool is disabled")
        if tool.manifest.requires_confirmation and not confirmed:
            return self._response(
                tool,
                "failed",
                started,
                trace_id,
                error="Tool requires confirmation before execution",
            )

        try:
            self._validate_arguments(tool, arguments)
            timeout = min(tool.manifest.timeout_seconds, self.settings.tool_timeout_seconds)
            output = await asyncio.wait_for(
                asyncio.to_thread(tool.handler, **arguments),
                timeout=timeout,
            )
        except TimeoutError:
            return self._response(
                tool,
                "timeout",
                started,
                trace_id,
                error="Tool execution timed out",
            )
        except HTTPException as exc:
            return self._response(tool, "failed", started, trace_id, error=str(exc.detail))
        except Exception as exc:
            return self._response(tool, "failed", started, trace_id, error=str(exc))

        limited_output = self._limit_output(tool, output)
        return self._response(tool, "success", started, trace_id, output=limited_output)

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

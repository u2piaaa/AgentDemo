import asyncio
from time import perf_counter
from typing import Any

from fastapi import HTTPException

from app.core.config import get_settings
from app.schemas import ToolRunResponse
from app.services.plugin_registry import RegisteredTool


class ToolExecutor:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(self, tool: RegisteredTool, arguments: dict[str, Any]) -> ToolRunResponse:
        self._validate_arguments(tool, arguments)
        timeout = min(tool.manifest.timeout_seconds, self.settings.tool_timeout_seconds)
        started = perf_counter()
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(tool.handler, **arguments),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=408, detail="Tool execution timed out") from exc
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        duration_ms = int((perf_counter() - started) * 1000)
        return ToolRunResponse(
            tool_name=tool.manifest.name,
            duration_ms=duration_ms,
            output=self._limit_output(output),
        )

    def _validate_arguments(self, tool: RegisteredTool, arguments: dict[str, Any]) -> None:
        required = tool.manifest.parameters.get("required", [])
        missing = [name for name in required if name not in arguments]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing required arguments: {missing}")

    def _limit_output(self, output: Any) -> Any:
        max_chars = self.settings.max_tool_output_chars
        if isinstance(output, str):
            return output[:max_chars]
        if isinstance(output, dict):
            return {
                key: value[:max_chars] if isinstance(value, str) else value
                for key, value in output.items()
            }
        return output

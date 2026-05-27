from pathlib import Path
import time

import pytest
from fastapi import HTTPException

from app.services.plugin_registry import PluginManifest, RegisteredTool
from app.services.tool_executor import ToolExecutor


def make_tool(
    parameters: dict,
    enabled: bool = True,
    requires_confirmation: bool = False,
    handler=None,
    timeout_seconds: int = 30,
) -> RegisteredTool:
    if handler is None:
        handler = lambda **kwargs: kwargs
    manifest = PluginManifest(
        name="example",
        description="Example tool.",
        parameters=parameters,
        entrypoint="tool.py:run",
        enabled=enabled,
        requires_confirmation=requires_confirmation,
        timeout_seconds=timeout_seconds,
    )
    return RegisteredTool(manifest=manifest, handler=handler, base_dir=Path("."))


def validate(arguments: dict, parameters: dict) -> None:
    ToolExecutor()._validate_arguments(make_tool(parameters), arguments)


def test_tool_argument_validation_accepts_valid_payload() -> None:
    validate(
        {"path": "README.md"},
        {
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string", "format": "path"}},
        },
    )


def test_tool_argument_validation_rejects_missing_required() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "Missing required arguments" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_wrong_type() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": 123},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "path must be string" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_unknown_fields() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": "README.md", "extra": True},
            {
                "type": "object",
                "required": ["path"],
                "additionalProperties": False,
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "Unknown arguments" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_format_errors() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": ""},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string", "format": "path"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "path must match format path" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_tool_run_returns_standard_success_result() -> None:
    tool = make_tool({"type": "object", "properties": {"text": {"type": "string"}}})

    result = await ToolExecutor().run(tool, {"text": "ok"})

    assert result.tool_name == "example"
    assert result.status == "success"
    assert result.output == {"text": "ok"}
    assert result.output_summary == '{"text": "ok"}'
    assert result.error is None
    assert result.duration_ms >= 0
    assert result.trace_id


@pytest.mark.asyncio
async def test_tool_run_returns_failed_for_disabled_tool() -> None:
    tool = make_tool({"type": "object"}, enabled=False)

    result = await ToolExecutor().run(tool, {})

    assert result.status == "failed"
    assert result.error == "Tool is disabled"
    assert result.output is None
    assert result.trace_id


@pytest.mark.asyncio
async def test_tool_run_returns_failed_when_confirmation_is_refused() -> None:
    tool = make_tool({"type": "object"}, requires_confirmation=True)

    result = await ToolExecutor().run(tool, {}, confirmed=False)

    assert result.status == "failed"
    assert result.error == "Tool requires confirmation before execution"


@pytest.mark.asyncio
async def test_tool_run_returns_failed_for_handler_exception() -> None:
    def handler() -> None:
        raise RuntimeError("boom")

    tool = make_tool({"type": "object"}, handler=handler)

    result = await ToolExecutor().run(tool, {})

    assert result.status == "failed"
    assert result.error == "boom"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_tool_run_returns_timeout_result() -> None:
    def handler() -> None:
        time.sleep(0.05)

    tool = make_tool({"type": "object"}, handler=handler, timeout_seconds=1)
    executor = ToolExecutor()
    executor.settings.tool_timeout_seconds = 0.01

    result = await executor.run(tool, {})

    assert result.status == "timeout"
    assert result.error == "Tool execution timed out"

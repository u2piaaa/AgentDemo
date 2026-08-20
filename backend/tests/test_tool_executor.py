import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.services.plugin_registry import TOOL_PROVIDER_MCP_SERVER, PluginManifest, RegisteredTool
from app.services.tool_executor import ToolExecutor


def make_tool(
    parameters: dict,
    enabled: bool = True,
    requires_confirmation: bool = False,
    handler=None,
    timeout_seconds: int = 30,
) -> RegisteredTool:
    if handler is None:
        def handler(**kwargs):
            return kwargs
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


class FakeMcpClient:
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict):
        return {
            "content": [{"type": "text", "text": "remote fetch failed"}],
            "isError": True,
        }


def make_mcp_tool() -> RegisteredTool:
    manifest = PluginManifest(
        name="mcp.fetch.fetch",
        description="Fetch a URL.",
        permission="network",
        parameters={
            "type": "object",
            "required": ["url"],
            "properties": {"url": {"type": "string"}},
        },
        entrypoint="mcp:call_tool",
    )
    return RegisteredTool(
        manifest=manifest,
        handler=None,
        base_dir=Path("."),
        provider=TOOL_PROVIDER_MCP_SERVER,
        provider_tool_id="fetch",
        server_name="fetch",
        client=FakeMcpClient(),
    )


def validate(arguments: dict, parameters: dict) -> None:
    ToolExecutor()._validate_arguments(make_tool(parameters), arguments)


class FakeAuditSession:
    def __init__(self) -> None:
        self.items = []
        self.commit_count = 0

    def add(self, item) -> None:
        self.items.append(item)

    async def commit(self) -> None:
        self.commit_count += 1


@pytest.fixture(autouse=True)
def reset_tool_timeout_setting():
    settings = get_settings()
    original_timeout = settings.tool_timeout_seconds
    settings.tool_timeout_seconds = 30
    yield
    settings.tool_timeout_seconds = original_timeout


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


def test_tool_summary_prefers_normalized_content() -> None:
    executor = ToolExecutor()

    summary = executor._summarize(
        {"content": "Fetched page body", "isError": False, "url": "https://example.com"}
    )

    assert summary == "Fetched page body"


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
async def test_mcp_is_error_result_returns_failed_status() -> None:
    result = await ToolExecutor().run(
        make_mcp_tool(),
        {"url": "https://example.com"},
        confirmed=True,
    )

    assert result.status == "failed"
    assert result.error == "remote fetch failed"
    assert result.output["content"] == "remote fetch failed"


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


@pytest.mark.asyncio
async def test_tool_run_records_success_audit() -> None:
    audit_session = FakeAuditSession()
    user_id = uuid4()
    conversation_id = uuid4()
    task_id = uuid4()
    tool = make_tool({"type": "object", "properties": {"text": {"type": "string"}}})

    result = await ToolExecutor().run(
        tool,
        {"text": "ok"},
        session=audit_session,  # type: ignore[arg-type]
        user_id=user_id,
        conversation_id=conversation_id,
        task_id=task_id,
    )

    assert result.status == "success"
    assert audit_session.commit_count == 1
    audit = audit_session.items[0]
    assert audit.user_id == user_id
    assert audit.conversation_id == conversation_id
    assert audit.task_id == task_id
    assert audit.tool_name == "example"
    assert audit.provider == "local_plugin"
    assert audit.status == "success"
    assert audit.input == {"text": "ok"}
    assert audit.input_summary == '{"text": "ok"}'
    assert audit.output_summary == '{"text": "ok"}'
    assert audit.error is None
    assert audit.duration_ms >= 0
    assert audit.trace_id == result.trace_id


@pytest.mark.asyncio
async def test_tool_run_records_exception_audit() -> None:
    def handler() -> None:
        raise RuntimeError("boom")

    audit_session = FakeAuditSession()
    tool = make_tool({"type": "object"}, handler=handler)

    result = await ToolExecutor().run(tool, {}, session=audit_session)  # type: ignore[arg-type]

    assert result.status == "failed"
    audit = audit_session.items[0]
    assert audit.status == "failed"
    assert audit.error == "boom"
    assert audit.output_summary is None
    assert audit.trace_id == result.trace_id


@pytest.mark.asyncio
async def test_tool_run_records_timeout_audit() -> None:
    def handler() -> None:
        time.sleep(0.05)

    audit_session = FakeAuditSession()
    tool = make_tool({"type": "object"}, handler=handler, timeout_seconds=1)
    executor = ToolExecutor()
    executor.settings.tool_timeout_seconds = 0.01

    result = await executor.run(tool, {}, session=audit_session)  # type: ignore[arg-type]

    assert result.status == "timeout"
    audit = audit_session.items[0]
    assert audit.status == "timeout"
    assert audit.error == "Tool execution timed out"
    assert audit.duration_ms >= 0
    assert audit.trace_id == result.trace_id

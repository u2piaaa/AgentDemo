import asyncio
import json

import anyio
import pytest
from fastapi import HTTPException

from app.mcp.tools import normalize_mcp_tool_result
from app.services.tool_executor import ToolExecutor
from test_tool_executor import FakeAuditSession, make_tool, make_mcp_tool


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [{"count": 0, "mode": "read"}]},
        {"items": [{"count": 1, "mode": "delete"}]},
        {"items": []},
        {"items": [{"count": 1, "mode": "read", "extra": True}]},
    ],
)
def test_nested_schema_constraints_are_enforced(payload):
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/item"}}
        },
        "$defs": {
            "item": {
                "type": "object",
                "required": ["count", "mode"],
                "additionalProperties": False,
                "properties": {
                    "count": {"type": "integer", "minimum": 1},
                    "mode": {"enum": ["read", "write"]},
                },
            }
        },
    }
    with pytest.raises(HTTPException, match="validation"):
        ToolExecutor()._validate_arguments(make_tool(schema), payload)


def test_external_schema_references_are_rejected_without_fetch():
    tool = make_tool(
        {"type": "object", "properties": {"value": {"$ref": "https://invalid.test/schema"}}}
    )
    with pytest.raises(HTTPException, match="unresolved local reference"):
        ToolExecutor()._validate_arguments(tool, {"value": "secret input"})


@pytest.mark.asyncio
async def test_schema_failure_is_audited_before_dispatch():
    dispatched = []
    tool = make_tool(
        {"type": "object", "properties": {"mode": {"enum": ["read"]}}},
        handler=lambda **kwargs: dispatched.append(kwargs),
    )
    session = FakeAuditSession()
    response = await ToolExecutor().run(tool, {"mode": "secret-invalid-value"}, session=session)
    assert response.status == "failed" and dispatched == []
    assert "secret-invalid-value" not in response.error
    assert session.items[0].status == "failed"


@pytest.mark.asyncio
async def test_structured_output_validated_preserved_and_errors_exempt():
    tool = make_mcp_tool()
    tool.manifest.output_schema = {
        "type": "object",
        "required": ["count"],
        "properties": {"count": {"type": "integer"}},
    }
    payload = {"structuredContent": {"count": 2}, "content": [{"type": "text", "text": "two"}]}

    class Client:
        async def call_tool(self, *args):
            return payload

    tool.client = Client()
    executor = ToolExecutor()
    response = await executor.run(tool, {"url": "https://example.com"}, confirmed=True)
    assert response.output["structuredContent"] == {"count": 2}
    payload["structuredContent"] = {"count": "invalid"}
    response = await executor.run(tool, {"url": "https://example.com"}, confirmed=True)
    assert response.status == "failed" and "structured output" in response.error
    payload["isError"] = True
    response = await executor.run(tool, {"url": "https://example.com"}, confirmed=True)
    assert response.status == "failed" and response.error == "two"


def test_nested_output_is_bounded_and_binary_is_omitted():
    normalized = normalize_mcp_tool_result(
        {
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "image", "mimeType": "image/png", "data": "x" * 10000},
                {"type": "resource_link", "uri": "https://example.com", "name": "source"},
            ],
            "structuredContent": {"rows": ["x" * 10000] * 100},
            "isError": True,
        }
    )
    assert "data" not in normalized["attachments"][0]
    assert normalized["attachments"][1]["uri"] == "https://example.com"
    tool = make_tool({"type": "object"})
    tool.manifest.output_strategy = {"max_chars": 500}
    output = ToolExecutor()._limit_output(tool, normalized)
    assert len(json.dumps(output)) < 600
    assert output["isError"] is True and output["_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("level_cancellation", [False, True])
async def test_cancelled_tool_records_unknown_outcome_and_does_not_retry(level_cancellation):
    started = asyncio.Event()
    calls = []
    tool = make_mcp_tool()

    class Client:
        async def call_tool(self, *args):
            calls.append(args)
            started.set()
            await asyncio.sleep(30)

    class Session(FakeAuditSession):
        async def rollback(self):
            await asyncio.sleep(0)

    tool.client = Client()
    session = Session()

    async def run():
        await ToolExecutor().run(
            tool, {"url": "https://example.com"}, confirmed=True, session=session
        )

    if level_cancellation:
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            await started.wait()
            group.cancel_scope.cancel()
    else:
        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1
    assert len(session.items) == 1 and session.items[0].status == "interrupted"
    assert "unknown" in session.items[0].error

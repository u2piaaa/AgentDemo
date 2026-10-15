from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import conversations
from app.api.routes.conversations import chat_stream, confirm_tool_stream, delete_conversation
from app.schemas import ChatRequest, ToolConfirmationRequest, ToolApprovalCancellation


class FakeResult:
    def __init__(self, scalar=None) -> None:
        self.scalar = scalar

    def scalar_one_or_none(self):
        return self.scalar


class FakeDeleteSession:
    def __init__(self, conversation=None) -> None:
        self.conversation = conversation
        self.statements = []
        self.deleted = None
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return FakeResult(self.conversation)
        return FakeResult()

    async def delete(self, item) -> None:
        self.deleted = item

    async def commit(self) -> None:
        self.committed = True


class FakeRuntime:
    seen_registries = []

    def __init__(self, *, plugin_registry, **kwargs) -> None:
        self.plugin_registry = plugin_registry
        self.seen_registries.append(plugin_registry)
        self.approvals = SimpleNamespace(claim=AsyncMock(return_value=SimpleNamespace(id=uuid4())))

    async def stream(self, payload):
        if False:
            yield {}

    async def stream_confirmed_tool(self, payload, *, approval):
        assert approval.id is not None
        if False:
            yield {}


def executed_table_names(session: FakeDeleteSession) -> list[str]:
    return [
        table.name
        for statement in session.statements
        if (table := getattr(statement, "table", None)) is not None
    ]


@pytest.mark.asyncio
async def test_chat_stream_uses_unified_tool_registry(monkeypatch) -> None:
    FakeRuntime.seen_registries = []
    monkeypatch.setattr(conversations, "AgentRuntime", FakeRuntime)
    tool_registry = SimpleNamespace(name="tool_registry")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                plugin_registry=SimpleNamespace(name="plugin_registry"),
                tool_registry=tool_registry,
            )
        )
    )

    await chat_stream(
        ChatRequest(message="hello"),
        request=request,  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=uuid4()),
        session=FakeDeleteSession(),  # type: ignore[arg-type]
    )

    assert FakeRuntime.seen_registries == [tool_registry]


@pytest.mark.asyncio
async def test_confirm_tool_stream_uses_unified_tool_registry(monkeypatch) -> None:
    FakeRuntime.seen_registries = []
    monkeypatch.setattr(conversations, "AgentRuntime", FakeRuntime)
    user_id = uuid4()
    conversation_id = uuid4()
    tool_registry = SimpleNamespace(name="tool_registry")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                plugin_registry=SimpleNamespace(name="plugin_registry"),
                tool_registry=tool_registry,
            )
        )
    )

    await confirm_tool_stream(
        ToolConfirmationRequest(
            conversation_id=conversation_id,
            message="read page",
            tool_name="mcp.fetch.fetch",
            arguments={"url": "https://example.com"},
        ),
        request=request,  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=user_id),
        session=FakeDeleteSession(SimpleNamespace(id=conversation_id, user_id=user_id)),  # type: ignore[arg-type]
    )

    assert FakeRuntime.seen_registries == [tool_registry]


async def test_stale_confirmation_is_rejected_before_sse_headers(monkeypatch):
    runtime = SimpleNamespace(
        approvals=SimpleNamespace(claim=AsyncMock(side_effect=HTTPException(409, "Already resolved"))),
        stream_confirmed_tool=AsyncMock(),
    )
    monkeypatch.setattr(conversations, "AgentRuntime", lambda **kwargs: runtime)
    user_id, conversation_id = uuid4(), uuid4()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tool_registry=None)))
    with pytest.raises(HTTPException) as exc:
        await confirm_tool_stream(
            ToolConfirmationRequest(conversation_id=conversation_id, message="read page", tool_name="fetch"),
            request=request, current_user=SimpleNamespace(id=user_id),
            session=FakeDeleteSession(SimpleNamespace(id=conversation_id, user_id=user_id)),
        )
    assert exc.value.status_code == 409
    runtime.stream_confirmed_tool.assert_not_called()


async def test_cancel_route_binds_current_owner_and_propagates_conflict(monkeypatch):
    store = SimpleNamespace(cancel=AsyncMock())
    monkeypatch.setattr(conversations, "ToolApprovalStore", lambda session: store)
    user_id, conversation_id, approval_id = uuid4(), uuid4(), uuid4()
    payload = ToolApprovalCancellation(conversation_id=conversation_id, approval_id=approval_id)
    result = await conversations.cancel_tool_approval(payload, SimpleNamespace(id=user_id), object())
    assert result == {"status": "cancelled"}
    store.cancel.assert_awaited_once_with(user_id, conversation_id, approval_id)
    store.cancel.side_effect = HTTPException(409, "Already resolved")
    with pytest.raises(HTTPException):
        await conversations.cancel_tool_approval(payload, SimpleNamespace(id=user_id), object())


@pytest.mark.asyncio
async def test_delete_conversation_cleans_related_records_before_delete() -> None:
    user = SimpleNamespace(id=uuid4())
    conversation = SimpleNamespace(id=uuid4(), user_id=user.id)
    session = FakeDeleteSession(conversation)

    await delete_conversation(conversation.id, current_user=user, session=session)  # type: ignore[arg-type]

    assert executed_table_names(session) == [
        "tasks",
        "tool_calls",
        "knowledge_chunks",
        "knowledge_documents",
        "memory_summaries",
        "messages",
    ]
    assert session.deleted is conversation
    assert session.committed is True


@pytest.mark.asyncio
async def test_delete_conversation_returns_404_for_unowned_conversation() -> None:
    session = FakeDeleteSession(conversation=None)

    with pytest.raises(HTTPException) as exc_info:
        await delete_conversation(
            uuid4(),
            current_user=SimpleNamespace(id=uuid4()),
            session=session,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
    assert executed_table_names(session) == []
    assert session.deleted is None
    assert session.committed is False

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.conversations import delete_conversation


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


def executed_table_names(session: FakeDeleteSession) -> list[str]:
    return [
        table.name
        for statement in session.statements
        if (table := getattr(statement, "table", None)) is not None
    ]


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

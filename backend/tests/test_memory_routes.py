from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.memory import (
    delete_memory_summary,
    disable_memory_summary,
    get_memory_summary,
    list_memory_summaries,
)
from app.models.conversation import MemorySummary
from app.schemas import MemorySummaryRead


def test_memory_summary_schema_marks_disabled_memory() -> None:
    summary = make_summary(valid_to=datetime.now(UTC))

    payload = MemorySummaryRead.model_validate(summary)

    assert payload.disabled is True


@pytest.mark.asyncio
async def test_delete_memory_summary_removes_it_from_visible_results() -> None:
    user_id = uuid4()
    summary = make_summary()
    session = FakeMemorySession(
        summaries=[summary],
        conversation_owners={summary.conversation_id: user_id},
    )

    await delete_memory_summary(summary.id, current_user=SimpleNamespace(id=user_id), session=session)
    visible = await list_memory_summaries(
        current_user=SimpleNamespace(id=user_id),
        session=session,
    )

    assert visible == []
    assert session.commits == 1


@pytest.mark.asyncio
async def test_user_cannot_access_another_users_memory() -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    summary = make_summary()
    session = FakeMemorySession(
        summaries=[summary],
        conversation_owners={summary.conversation_id: owner_id},
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_memory_summary(
            summary.id,
            current_user=SimpleNamespace(id=other_user_id),
            session=session,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_disable_memory_summary_hides_it_from_default_list() -> None:
    user_id = uuid4()
    summary = make_summary()
    session = FakeMemorySession(
        summaries=[summary],
        conversation_owners={summary.conversation_id: user_id},
    )

    disabled = await disable_memory_summary(
        summary.id,
        current_user=SimpleNamespace(id=user_id),
        session=session,
    )
    visible = await list_memory_summaries(
        current_user=SimpleNamespace(id=user_id),
        session=session,
    )
    all_summaries = await list_memory_summaries(
        current_user=SimpleNamespace(id=user_id),
        include_disabled=True,
        session=session,
    )

    assert disabled.disabled is True
    assert visible == []
    assert all_summaries == [summary]


def make_summary(valid_to: datetime | None = None) -> MemorySummary:
    now = datetime.now(UTC)
    return MemorySummary(
        id=uuid4(),
        conversation_id=uuid4(),
        summary="The user prefers terse implementation notes.",
        valid_from=now,
        valid_to=valid_to,
        created_at=now,
        updated_at=now,
    )


class FakeMemorySession:
    def __init__(
        self,
        summaries: list[MemorySummary],
        conversation_owners: dict[UUID, UUID],
    ) -> None:
        self.summaries = summaries
        self.conversation_owners = conversation_owners
        self.commits = 0

    async def execute(self, statement):
        params = statement.compile().params
        user_id = params.get("user_id_1")
        summary_id = params.get("id_1")
        conversation_id = params.get("conversation_id_1")
        include_disabled = "valid_to IS NULL" not in str(statement)
        rows = [
            summary
            for summary in self.summaries
            if self.conversation_owners.get(summary.conversation_id) == user_id
            and (summary_id is None or summary.id == summary_id)
            and (conversation_id is None or summary.conversation_id == conversation_id)
            and (include_disabled or summary.valid_to is None)
        ]
        return FakeResult(rows)

    async def delete(self, summary: MemorySummary) -> None:
        self.summaries = [item for item in self.summaries if item.id != summary.id]

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, summary: MemorySummary) -> None:
        return None


class FakeResult:
    def __init__(self, rows: list[MemorySummary]) -> None:
        self.rows = rows

    def scalars(self):
        return self

    def all(self) -> list[MemorySummary]:
        return self.rows

    def scalar_one_or_none(self) -> MemorySummary | None:
        if not self.rows:
            return None
        if len(self.rows) > 1:
            raise AssertionError("Expected at most one row")
        return self.rows[0]

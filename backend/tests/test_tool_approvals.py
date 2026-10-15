"""Exercise approval ownership and row locks against real PostgreSQL, never SQLite.

Set TEST_DATABASE_URL to a migrated, dedicated test database. CI runs these on
every supported Python version. Each test deletes only its own synthetic user.
"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Conversation, Message, User
from app.schemas import AgentExecutionState, ToolConfirmationRequest
from app.services.model_gateway import ModelRoute
from app.services.tool_approvals import ToolApprovalStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def approval_database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, conversation_id = uuid4(), uuid4()
    async with sessions() as session:
        username = f"approval_{user_id.hex[:20]}"
        session.add(User(id=user_id, username=username, username_normalized=username,
                         password_hash="integration-fixture-not-a-credential"))
        await session.flush()
        session.add(Conversation(id=conversation_id, user_id=user_id))
        await session.commit()
    try:
        yield sessions, user_id, conversation_id
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


async def create_approval(database):
    sessions, user_id, conversation_id = database
    state = AgentExecutionState(
        conversation_id=conversation_id, user_id=user_id, message="Read README.md",
        trace_id=uuid4().hex,
        observations=["Earlier tool result", "Tool requires confirmation before execution"],
        tool_calls=[
            {"tool_name": "earlier", "arguments": {}, "result": {"status": "success"}},
            {"tool_name": "read_file", "arguments": {"path": "README.md"},
             "requires_confirmation": True, "reason": "Read the requested file",
             "result": {"status": "failed", "error": "Tool requires confirmation before execution"}},
        ],
    )
    async with sessions() as session:
        await ToolApprovalStore(session).create(state, ModelRoute("fixture", "local", "test"),
                                                "conversation", 3600)
    return ToolConfirmationRequest(
        approval_id=UUID(state.tool_calls[-1]["approval_id"]),
        conversation_id=conversation_id, message=state.message, tool_name="read_file",
        arguments={"path": "README.md"},
    )


async def stored(database, request):
    async with database[0]() as session:
        return await session.get(Message, request.approval_id)


async def claim(database, request, user_id=None):
    async with database[0]() as session:
        return await ToolApprovalStore(session).claim(user_id or database[1], request)


async def test_restart_restores_snapshot_and_claim_is_committed_before_effect(approval_database):
    request = await create_approval(approval_database)
    restored = await stored(approval_database, request)
    assert restored.content == ""
    assert restored.metadata_["approval"]["status"] == "pending"
    assert restored.metadata_["continuation"]["observations"] == ["Earlier tool result"]
    assert [call["tool_name"] for call in restored.metadata_["continuation"]["tool_calls"]] == ["earlier"]
    await claim(approval_database, request)
    assert (await stored(approval_database, request)).metadata_["approval"]["status"] == "approved"
    with pytest.raises(HTTPException, match="already been resolved"):
        await claim(approval_database, request)


@pytest.mark.parametrize("field,value", [
    ("tool_name", "other_tool"), ("arguments", {"path": "secret.txt"}),
    ("message", "Different instruction"), ("task_type", "coding"),
])
async def test_tampered_confirmation_is_rejected_without_consuming_approval(approval_database, field, value):
    request = await create_approval(approval_database)
    with pytest.raises(HTTPException) as exc:
        await claim(approval_database, request.model_copy(update={field: value}))
    assert exc.value.status_code == 409
    assert (await stored(approval_database, request)).metadata_["approval"]["status"] == "pending"
    await claim(approval_database, request)


async def test_owner_and_conversation_are_both_bound(approval_database):
    request = await create_approval(approval_database)
    with pytest.raises(HTTPException):
        await claim(approval_database, request, user_id=uuid4())
    with pytest.raises(HTTPException):
        await claim(approval_database, request.model_copy(update={"conversation_id": uuid4()}))
    with pytest.raises(HTTPException):
        await claim(approval_database, request.model_copy(update={"approval_id": uuid4()}))
    assert (await stored(approval_database, request)).metadata_["approval"]["status"] == "pending"


async def test_cancel_is_durable_idempotent_and_cannot_be_confirmed(approval_database):
    request = await create_approval(approval_database)
    for _ in range(2):
        async with approval_database[0]() as session:
            await ToolApprovalStore(session).cancel(approval_database[1], request.conversation_id,
                                                    request.approval_id)
    restored = await stored(approval_database, request)
    assert restored.metadata_["approval"]["status"] == "cancelled"
    assert restored.metadata_["tool_calls"][-1]["approval_status"] == "cancelled"
    with pytest.raises(HTTPException):
        await claim(approval_database, request)


async def test_expiration_is_persisted(approval_database):
    request = await create_approval(approval_database)
    async with approval_database[0]() as session:
        message = await session.get(Message, request.approval_id)
        metadata = deepcopy(message.metadata_)
        metadata["approval"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        message.metadata_ = metadata
        await session.commit()
    with pytest.raises(HTTPException, match="expired"):
        await claim(approval_database, request)
    assert (await stored(approval_database, request)).metadata_["approval"]["status"] == "expired"


async def test_legacy_client_requires_exact_pending_stored_plan(approval_database):
    request = await create_approval(approval_database)
    legacy = request.model_copy(update={"approval_id": None})
    with pytest.raises(HTTPException):
        await claim(approval_database, legacy.model_copy(update={"arguments": {"path": "other"}}))
    await claim(approval_database, legacy)
    with pytest.raises(HTTPException):
        await claim(approval_database, legacy)


@pytest.mark.parametrize("legacy", [False, True])
async def test_concurrent_confirmations_only_one_can_reach_effect(approval_database, legacy):
    request = await create_approval(approval_database)
    if legacy:
        request = request.model_copy(update={"approval_id": None})
    reached_effect = []

    async def continue_tool():
        try:
            approved = await claim(approval_database, request)
            reached_effect.append(approved.id)
            return "claimed"
        except HTTPException as exc:
            return exc.status_code

    results = await asyncio.wait_for(asyncio.gather(continue_tool(), continue_tool()), timeout=5)
    assert sorted(map(str, results)) == ["409", "claimed"]
    assert len(reached_effect) == 1


async def test_cancel_and_confirm_race_has_one_winner(approval_database):
    request = await create_approval(approval_database)

    async def cancel():
        async with approval_database[0]() as session:
            await ToolApprovalStore(session).cancel(approval_database[1], request.conversation_id,
                                                    request.approval_id)

    results = await asyncio.wait_for(asyncio.gather(
        claim(approval_database, request), cancel(), return_exceptions=True,
    ), timeout=5)
    assert sum(isinstance(result, HTTPException) for result in results) == 1
    assert (await stored(approval_database, request)).metadata_["approval"]["status"] in {"approved", "cancelled"}


async def test_interruption_never_requeues_consumed_approval(approval_database):
    request = await create_approval(approval_database)
    async with approval_database[0]() as session:
        store = ToolApprovalStore(session)
        message = await store.claim(approval_database[1], request)
        await store.interrupt(message)
    restored = await stored(approval_database, request)
    assert restored.metadata_["approval"]["status"] == "interrupted"
    assert "may already have completed" in restored.metadata_["approval"]["error"]
    with pytest.raises(HTTPException):
        await claim(approval_database, request)

"""Retrieval and compaction acceptance against real PostgreSQL/pgvector."""

import asyncio
from datetime import UTC, datetime, timedelta
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.memory import delete_memory_summary, disable_memory_summary
from app.api.routes.knowledge import reindex_document as reindex_document_route
from fastapi import HTTPException
from app.core.config import Settings
from app.models import Conversation, Message, User
from app.models.conversation import MemorySummary
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.schemas import KnowledgeDocumentCreate
from app.services.memory import MemoryService
from app.services.rag import RagService

pytestmark = pytest.mark.integration


@pytest.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    users, conversations = [uuid4(), uuid4()], [uuid4(), uuid4()]
    async with sessions() as session:
        for user_id, conversation_id in zip(users, conversations, strict=True):
            name = f"rag_{user_id.hex[:24]}"
            session.add(
                User(
                    id=user_id,
                    username=name,
                    username_normalized=name,
                    password_hash="synthetic-integration-fixture",
                )
            )
            await session.flush()
            session.add(Conversation(id=conversation_id, user_id=user_id))
        await session.commit()
    try:
        yield sessions, users, conversations
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id.in_(users)))
            await session.commit()
        await engine.dispose()


async def document(session, user_id, conversation_id, texts, vectors=None, title="Fixture"):
    doc = KnowledgeDocument(
        user_id=user_id,
        conversation_id=conversation_id,
        title=title,
        source_type="text",
        status="indexed",
    )
    session.add(doc)
    await session.flush()
    for index, content in enumerate(texts):
        session.add(
            KnowledgeChunk(
                document_id=doc.id,
                chunk_index=index,
                content=content,
                embedding=vectors[index] if vectors else None,
            )
        )
    await session.commit()
    return doc


async def offline(texts):
    return []


async def query_embedding(texts):
    return [[1.0] + [0.0] * 1535 for _ in texts]


async def test_lexical_retrieval_finds_evidence_after_first_fifty_chunks(database):
    sessions, users, conversations = database
    async with sessions() as session:
        doc = await document(
            session,
            users[0],
            conversations[0],
            [f"Unrelated filler {i}" for i in range(75)] + ["故障编号 ZXQ-4821 的恢复口令是星河。"],
        )
        service = RagService(session, users[0])
        service._embed_or_empty = offline
        results = await service.search("ZXQ-4821 故障编号", conversations[0])
        assert [(item.document_id, item.chunk_index) for item in results] == [(str(doc.id), 75)]


async def test_hybrid_fusion_promotes_exact_fact_alongside_semantic_results(database):
    sessions, users, conversations = database
    async with sessions() as session:
        await document(
            session,
            users[0],
            conversations[0],
            ["General incident recovery process."],
            [[1.0] + [0.0] * 1535],
            title="Semantic decoy",
        )
        exact = await document(
            session,
            users[0],
            None,
            ["ZXQ-4821 recovery requires key Aurora."],
            [[0.6, 0.8] + [0.0] * 1534],
            title="Exact evidence",
        )
        service = RagService(session, users[0])
        service._embed_or_empty = query_embedding
        results = await service.search("ZXQ-4821", conversations[0])
        assert results[0].document_id == str(exact.id)
        assert results[0].retrieval_method == "hybrid_rrf"
        assert results[0].evidence["keyword_rank"] == 1
        assert results[0].evidence["vector_rank"] == 2


async def test_every_retrieval_path_scopes_owner_conversation_and_status(database):
    sessions, users, conversations = database
    async with sessions() as session:
        allowed = await document(
            session, users[0], conversations[0], ["Aurora local"], await query_embedding([""])
        )
        global_doc = await document(
            session, users[0], None, ["Aurora global"], await query_embedding([""])
        )
        await document(
            session,
            users[1],
            conversations[0],
            ["Aurora foreign owner"],
            await query_embedding([""]),
        )
        await document(
            session, users[1], None, ["Aurora foreign global"], await query_embedding([""])
        )
        await document(
            session,
            users[0],
            conversations[1],
            ["Aurora other conversation"],
            await query_embedding([""]),
        )
        pending = await document(
            session, users[0], None, ["Aurora pending"], await query_embedding([""])
        )
        pending.status = "pending"
        await session.commit()
        service = RagService(session, users[0])
        service._embed_or_empty = query_embedding
        for results in (
            await service._search_by_keywords("Aurora", conversations[0], 20),
            await service._search_by_vector("Aurora", conversations[0], 20),
        ):
            assert {item.document_id for item in results} == {str(allowed.id), str(global_doc.id)}
        results = await service._conversation_document_context(conversations[0], 20)
        assert [item.document_id for item in results] == [str(allowed.id)]
        anonymous = RagService(session)
        assert await anonymous._search_by_keywords("Aurora", None, 20) == []


async def test_indexing_preserves_provenance_and_reports_embedding_outage(database):
    sessions, users, conversations = database
    async with sessions() as session:
        service = RagService(session, users[0])
        service._embed_or_empty = offline
        content = "# Aurora\n\n" + "    recovery code: 星河\n\n" * 80
        doc = await service.index_text(
            KnowledgeDocumentCreate(
                title="Runbook", content=content, conversation_id=conversations[0]
            )
        )
        assert doc.retrieval_mode == "keyword"
        assert doc.metadata_["embedded_chunks"] == 0
        chunks = (
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc.id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        ).all()
        for chunk in chunks:
            assert (
                chunk.content
                == content.strip()[chunk.metadata_["start_char"] : chunk.metadata_["end_char"]]
            )
        assert chunks[-1].metadata_["end_char"] == len(content.strip())


async def test_contextual_retrieval_finds_title_and_section_omitted_from_chunk_body(database):
    sessions, users, conversations = database
    async with sessions() as session:
        service = RagService(session, users[0])
        inputs = []

        async def record_embeddings(texts):
            inputs.extend(texts)
            return []

        service._embed_or_empty = record_embeddings
        doc = await service.index_text(
            KnowledgeDocumentCreate(
                title="Orion Cluster Guide",
                conversation_id=conversations[0],
                content="# Recovery\n\n" + "Restart the worker safely. " * 100,
            )
        )
        assert all("Orion Cluster Guide" in text and "Recovery" in text for text in inputs)
        results = await service._search_by_keywords("Orion", conversations[0], 20)
        assert results and all(item.document_id == str(doc.id) for item in results)
        assert all("Orion" not in item.content for item in results)
        assert all("Orion" in item.evidence["retrieval_context"] for item in results)


async def test_existing_documents_reindex_without_changing_citation_ids_or_text(database):
    sessions, users, conversations = database
    async with sessions() as session:
        doc = await document(
            session,
            users[0],
            conversations[0],
            ["# Recovery\n\nWorker step.", "Database step."],
            title="Orion",
        )
        original = list(
            (
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == doc.id)
                    .order_by(KnowledgeChunk.chunk_index)
                )
            ).all()
        )
        original_values = [(chunk.id, chunk.content, chunk.chunk_index) for chunk in original]
        service = RagService(session, users[0])
        service._embed_or_empty = query_embedding
        updated = await service.reindex_document(doc.id)
        assert updated.retrieval_mode == "hybrid"
        assert [
            (chunk.id, chunk.content, chunk.chunk_index) for chunk in original
        ] == original_values
        assert all(
            "Orion" in chunk.metadata_["retrieval_context"]
            and "Recovery" in chunk.metadata_["retrieval_context"]
            for chunk in original
        )
        assert all(chunk.embedding is not None for chunk in original)
        doc_id = doc.id
        with pytest.raises(HTTPException) as error:
            await reindex_document_route(
                doc_id, current_user=SimpleNamespace(id=users[1]), session=session
            )
        assert error.value.status_code == 404


async def test_reindex_outage_preserves_preexisting_vector_index(database):
    sessions, users, conversations = database
    async with sessions() as session:
        doc = await document(
            session,
            users[0],
            conversations[0],
            ["Saved vector evidence"],
            await query_embedding([""]),
        )
        doc_id = doc.id
        service = RagService(session, users[0])
        service._embed_or_empty = offline
        with pytest.raises(ValueError, match="existing index preserved"):
            await service.reindex_document(doc_id)
        await session.rollback()
        chunk = await session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
        )
        assert chunk.embedding is not None
        assert chunk.metadata_ == {}


async def test_reranker_only_receives_scoped_candidates_and_controls_final_order(
    database, monkeypatch
):
    sessions, users, conversations = database
    async with sessions() as session:
        first = await document(
            session, users[0], conversations[0], ["Aurora recovery worker steps"]
        )
        second = await document(
            session, users[0], conversations[0], ["Aurora database restore backup"]
        )
        await document(session, users[1], None, ["Aurora PRIVATE FOREIGN"])
        seen = []

        async def rank(self, query, documents):
            seen.extend(documents)
            return [(1, 0.99), (0, 0.1)]

        monkeypatch.setattr("app.services.rag.RetrievalReranker.rank", rank)
        service = RagService(session, users[0])
        service._embed_or_empty = offline
        baseline = await service._search_by_keywords("Aurora", conversations[0], 24)
        results = await service.search("Aurora", conversations[0])
        assert results[0].document_id == baseline[1].document_id
        assert {item.document_id for item in results} == {str(first.id), str(second.id)}
        assert len(seen) == 2 and not any("PRIVATE" in item for item in seen)
        assert results[0].evidence["rerank_status"] == "applied"


class Gateway:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = None
        self.error = None

    async def summarize_messages(self, messages, existing_summary=None):
        self.calls.append((messages, existing_summary))
        self.entered.set()
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return "Retained durable facts."


async def messages(session, conversation_id, start, count, long_first=False):
    for i in range(start, start + count):
        session.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content="x" * 2500 if long_first and i == start else f"Fact {i}",
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i),
            )
        )
    await session.commit()


def memory(session, user_id, gateway=None, **settings):
    return MemoryService(
        session, gateway or Gateway(), Settings(agent_memory_message_limit=2, **settings), user_id
    )


async def test_memory_cursor_only_summarizes_new_old_messages_and_survives_restart(database):
    sessions, users, conversations = database
    gateway = Gateway()
    async with sessions() as session:
        await messages(session, conversations[0], 0, 8)
        assert await memory(session, users[0], gateway).update(conversations[0])
        assert [item["content"] for item in gateway.calls[0][0]] == [f"Fact {i}" for i in range(6)]
    async with sessions() as session:
        assert not await memory(session, users[0], gateway).update(conversations[0])
        await messages(session, conversations[0], 8, 2)
        assert await memory(session, users[0], gateway).update(conversations[0])
        assert [item["content"] for item in gateway.calls[1][0]] == ["Fact 6", "Fact 7"]
        assert gateway.calls[1][1] == "Retained durable facts."
        conversation = await session.get(Conversation, conversations[0])
        assert conversation.metadata_["memory_cursor"]["message_count"] == 8


async def test_memory_large_message_is_consumed_in_bounded_parts_without_gaps(database):
    sessions, users, conversations = database
    gateway = Gateway()
    async with sessions() as session:
        await messages(session, conversations[0], 0, 3, long_first=True)
        for _ in range(3):
            assert await memory(
                session, users[0], gateway, agent_memory_input_max_chars=1000
            ).update(conversations[0])
        assert not await memory(session, users[0], gateway).update(conversations[0])
        parts = [call[0][0]["content"] for call in gateway.calls]
        assert [len(part) for part in parts] == [1000, 1000, 500]
        assert "".join(parts) == "x" * 2500


@pytest.mark.parametrize("action", ["disable", "delete"])
async def test_forgotten_memory_is_not_regenerated_from_processed_history(database, action):
    sessions, users, conversations = database
    gateway = Gateway()
    async with sessions() as session:
        await messages(session, conversations[0], 0, 4)
        assert await memory(session, users[0], gateway).update(conversations[0])
        summary = await session.scalar(
            select(MemorySummary).where(MemorySummary.conversation_id == conversations[0])
        )
        operation = disable_memory_summary if action == "disable" else delete_memory_summary
        await operation(summary.id, current_user=SimpleNamespace(id=users[0]), session=session)
        assert not await memory(session, users[0], gateway).update(conversations[0])
        await messages(session, conversations[0], 4, 2)
        assert await memory(session, users[0], gateway).update(conversations[0])
        assert gateway.calls[-1][1] is None
        assert [item["content"] for item in gateway.calls[-1][0]] == ["Fact 2", "Fact 3"]


async def test_memory_concurrent_workers_do_not_duplicate_or_overwrite_compaction(database):
    sessions, users, conversations = database
    gateway = Gateway()
    gateway.release = asyncio.Event()
    async with sessions() as first, sessions() as second:
        await messages(first, conversations[0], 0, 4)
        task = asyncio.create_task(memory(first, users[0], gateway).update(conversations[0]))
        try:
            await asyncio.wait_for(gateway.entered.wait(), 2)
            assert not await memory(second, users[0], gateway).update(conversations[0])
            gateway.release.set()
            assert await task
            assert len(gateway.calls) == 1
        finally:
            gateway.release.set()
            await task


async def test_memory_timeout_and_wrong_owner_never_advance_cursor(database):
    sessions, users, conversations = database
    async with sessions() as session:
        await messages(session, conversations[0], 0, 4)
        assert not await memory(session, users[1]).update(conversations[0])
        gateway = Gateway()
        gateway.release = asyncio.Event()
        assert not await memory(
            session, users[0], gateway, agent_memory_timeout_seconds=0.05
        ).update(conversations[0])
        conversation = await session.get(Conversation, conversations[0])
        assert "memory_cursor" not in conversation.metadata_
        assert await memory(session, users[0]).update(conversations[0])


async def test_cancelled_compaction_releases_lock_without_consuming_source(database):
    sessions, users, conversations = database
    async with sessions() as session:
        await messages(session, conversations[0], 0, 4)
        gateway = Gateway()
        gateway.release = asyncio.Event()
        task = asyncio.create_task(memory(session, users[0], gateway).update(conversations[0]))
        await asyncio.wait_for(gateway.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with sessions() as session:
        assert await memory(session, users[0]).update(conversations[0])


@pytest.mark.parametrize("action", ["disable", "delete"])
async def test_retiring_legacy_memory_does_not_replay_old_facts(database, action):
    sessions, users, conversations = database
    async with sessions() as session:
        await messages(session, conversations[0], 0, 4)
        summary = MemorySummary(
            conversation_id=conversations[0], summary="Legacy facts without cursor"
        )
        session.add(summary)
        await session.commit()
        operation = disable_memory_summary if action == "disable" else delete_memory_summary
        await operation(summary.id, current_user=SimpleNamespace(id=users[0]), session=session)
        assert not await memory(session, users[0]).update(conversations[0])


@pytest.mark.parametrize(
    "reply",
    ["   ", "x" * 8001, "<tool_calls>internal</tool_calls>"],
    ids=["empty", "oversized", "protocol"],
)
async def test_invalid_memory_output_is_not_saved_or_marked_processed(database, reply):
    sessions, users, conversations = database
    async with sessions() as session:
        await messages(session, conversations[0], 0, 4)

        async def invalid(messages, existing_summary=None):
            return reply

        gateway = Gateway()
        gateway.summarize_messages = invalid
        assert not await memory(session, users[0], gateway).update(conversations[0])
        conversation = await session.get(Conversation, conversations[0])
        assert "memory_cursor" not in conversation.metadata_

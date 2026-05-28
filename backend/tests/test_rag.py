from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.rag import RagService


def test_split_text_uses_overlap() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]
    chunks = service._split_text("a" * 1000, chunk_size=500, overlap=50)

    assert len(chunks) == 3
    assert chunks[1].startswith("a" * 50)


def test_vector_citation_includes_score_and_metadata() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]
    document = KnowledgeDocument(
        id=uuid4(),
        title="Vector Guide",
        source_type="markdown",
        source_uri=None,
        status="indexed",
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=2,
        content="Vector retrieval keeps similarity scores.",
    )

    citation = service._citation(
        chunk,
        document,
        score=service._vector_score(0.25),
        retrieval_method="vector",
    )

    assert citation.score == 0.75
    assert citation.retrieval_method == "vector"
    assert citation.source_type == "markdown"
    assert citation.model_dump()["metadata"] == {
        "document_title": "Vector Guide",
        "chunk_index": 2,
        "source_type": "markdown",
        "score": 0.75,
        "retrieval_method": "vector",
    }


@pytest.mark.asyncio
async def test_keyword_fallback_keeps_score_when_embedding_unavailable() -> None:
    conversation_id = uuid4()
    document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=conversation_id,
        title="Keyword Guide",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.utcnow(),
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        content="alpha beta gamma",
    )
    service = RagService(session=FakeSession([(chunk, document)]))  # type: ignore[arg-type]

    async def no_embeddings(texts: list[str]) -> list[list[float]]:
        return []

    service._embed_or_empty = no_embeddings  # type: ignore[method-assign]

    citations = await service.search("alpha beta", conversation_id=conversation_id)

    assert len(citations) == 1
    assert citations[0].retrieval_method == "keyword"
    assert citations[0].score == 2.0


@pytest.mark.asyncio
async def test_conversation_documents_are_prioritized_over_global_keyword_results() -> None:
    conversation_id = uuid4()
    local_document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=conversation_id,
        title="Local Notes",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.utcnow(),
    )
    global_document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=None,
        title="Global Notes",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.utcnow(),
    )
    local_chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=local_document.id,
        chunk_index=0,
        content="alpha only",
    )
    global_chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=global_document.id,
        chunk_index=0,
        content="alpha beta",
    )
    service = RagService(
        session=FakeSession([(global_chunk, global_document), (local_chunk, local_document)])
    )  # type: ignore[arg-type]

    citations = await service._search_by_keywords("alpha beta", conversation_id, limit=2)

    assert [citation.title for citation in citations] == ["Local Notes", "Global Notes"]
    assert [citation.score for citation in citations] == [1.0, 2.0]


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement):
        return SimpleNamespace(all=lambda: self.rows)

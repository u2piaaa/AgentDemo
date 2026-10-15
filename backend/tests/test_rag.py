from datetime import UTC, datetime
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.rag import Citation, RagService


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
        "source_uri": None,
        "score": 0.75,
        "retrieval_method": "vector",
    }


def test_mcp_resource_citation_includes_source_uri() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]
    document = KnowledgeDocument(
        id=uuid4(),
        title="MCP Resource",
        source_type="mcp_resource",
        source_uri="mcp://fake/doc",
        status="indexed",
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        content="MCP resource citation.",
    )

    citation = service._citation(chunk, document, score=1.0, retrieval_method="keyword")

    assert citation.source_type == "mcp_resource"
    assert citation.source_uri == "mcp://fake/doc"
    assert citation.model_dump()["metadata"]["source_uri"] == "mcp://fake/doc"


@pytest.mark.asyncio
async def test_vector_search_drops_low_similarity_results() -> None:
    document = KnowledgeDocument(
        id=uuid4(),
        title="Unrelated notes",
        source_type="text",
        source_uri=None,
        status="indexed",
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        content="An unrelated project fact.",
    )
    service = RagService(session=FakeSession([(chunk, document, 0.7)]))  # type: ignore[arg-type]

    async def embeddings(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2]]

    service._embed_or_empty = embeddings  # type: ignore[method-assign]

    citations = await service._search_by_vector("simple arithmetic", None, limit=4)

    assert citations == []


def test_conversation_context_fallback_requires_document_intent() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]

    assert service._requests_conversation_context("Use the uploaded document") is True
    assert service._requests_conversation_context("只根据这份资料回答") is True
    assert service._requests_conversation_context("What is nine times nine?") is False


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
        created_at=datetime.now(UTC),
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
async def test_keyword_fallback_matches_chinese_when_embedding_unavailable() -> None:
    conversation_id = uuid4()
    document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=conversation_id,
        title="项目笔记",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.now(UTC),
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        content="项目代号叫 Aurora，首选数据库是 PostgreSQL。",
    )
    service = RagService(session=FakeSession([(chunk, document)]))  # type: ignore[arg-type]

    async def no_embeddings(texts: list[str]) -> list[list[float]]:
        return []

    service._embed_or_empty = no_embeddings  # type: ignore[method-assign]

    citations = await service.search("项目代号是什么？", conversation_id=conversation_id)

    assert len(citations) == 1
    assert citations[0].title == "项目笔记"
    assert citations[0].retrieval_method == "keyword"
    assert citations[0].score >= 2.0


def test_keyword_terms_are_bounded_and_ignore_common_question_noise() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]

    terms = service._keyword_terms("请问项目代号是什么？ Please tell me about Aurora.")

    assert "项目" in terms
    assert "代号" in terms
    assert "aurora" in terms
    assert "什么" not in terms
    assert "please" not in terms
    assert len(terms) <= 32


@pytest.mark.asyncio
async def test_keyword_relevance_precedes_conversation_scope_tiebreaker() -> None:
    conversation_id = uuid4()
    local_document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=conversation_id,
        title="Local Notes",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.now(UTC),
    )
    global_document = KnowledgeDocument(
        id=uuid4(),
        conversation_id=None,
        title="Global Notes",
        source_type="text",
        source_uri=None,
        status="indexed",
        created_at=datetime.now(UTC),
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

    assert [citation.title for citation in citations] == ["Global Notes", "Local Notes"]
    assert [citation.score for citation in citations] == [2.0, 1.0]


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement):
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.parametrize("size,overlap", [(0, 0), (50, 50), (50, -1), (20, 40)])
def test_chunker_rejects_non_progressing_windows(size, overlap):
    with pytest.raises(ValueError):
        RagService(None)._split_text("content", size, overlap)


def test_chunk_spans_cover_text_preserving_paragraphs_and_code_indentation():
    service = RagService(None)
    content = "# Header\n\n" + "    code = 'value'\n\nParagraph sentence.\n\n" * 60
    clean = content.strip()
    spans = service._split_spans(content, chunk_size=300, overlap=40)
    covered = set()
    for text, start, end in spans:
        assert text == clean[start:end]
        assert len(text) <= 300
        covered.update(range(start, end))
    assert len(covered) == len(clean)
    assert "    code" in spans[0][0]
    assert "\n\n" in spans[0][0]


@pytest.mark.asyncio
async def test_embedding_timeout_keeps_keyword_evidence():
    doc = KnowledgeDocument(id=uuid4(), title="Exact", source_type="text", status="indexed")
    chunk = KnowledgeChunk(id=uuid4(), document_id=doc.id, chunk_index=0, content="ZXQ-4821")
    service = RagService(FakeSession([(chunk, doc)]))
    service.model_gateway.settings = service.model_gateway.settings.model_copy(
        update={"agent_retrieval_timeout_seconds": 0.02}
    )

    async def slow(texts):
        await asyncio.sleep(5)
        return []

    service._embed_or_empty = slow
    results = await asyncio.wait_for(service.search("ZXQ-4821"), 0.5)
    assert results[0].retrieval_method == "keyword"


@pytest.mark.asyncio
async def test_wrong_storage_dimension_falls_back_to_keyword():
    service = RagService(None)

    async def vectors(texts):
        return [[1.0, 0.0]]

    service.model_gateway.embed_texts = vectors
    assert await service._embed_or_empty(["text"]) == []


def test_contextual_chunks_keep_section_lineage_without_modifying_evidence():
    service = RagService(None)
    text = "# Aurora runbook\n\n## Regional failover\n\n" + "Worker recovery steps. " * 90
    spans = service._split_spans(text)
    contexts = service._chunk_contexts("Operations 2026", text, spans)
    assert len(contexts) == len(spans)
    assert all(
        "Operations 2026" in context and "Regional failover" in context for context in contexts
    )
    assert "Regional failover" not in spans[-1][0]
    assert all(len(context) <= 700 for context in contexts)


def test_rrf_equal_vector_scores_do_not_override_stronger_lexical_evidence():
    from dataclasses import replace

    service = RagService(None)
    first = Citation("a", "Generic", 0, "First", "text", None, 1.0, "vector")
    exact = Citation("b", "Specific", 0, "Exact", "text", None, 1.0, "vector")
    results = service._fuse(
        [first, exact],
        [
            replace(exact, score=5.0, retrieval_method="keyword"),
            replace(first, score=1.0, retrieval_method="keyword"),
        ],
        2,
    )
    assert [item.document_id for item in results] == ["b", "a"]
    assert [item.evidence["vector_rank"] for item in results] == [1, 1]

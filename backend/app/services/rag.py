from dataclasses import dataclass, replace
import asyncio
import hashlib
import math
import re
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.schemas import KnowledgeDocumentCreate
from app.services.model_gateway import ModelGateway
from app.services.retrieval_reranker import RetrievalReranker, diverse_indices


MIN_VECTOR_SCORE = 0.45
MAX_KEYWORD_TERMS = 32
MAX_RESULTS = 20
MAX_CANDIDATES = 120
RRF_K = 60
LATIN_KEYWORD_STOP_TERMS = {
    "about",
    "could",
    "from",
    "have",
    "please",
    "tell",
    "that",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}
CJK_KEYWORD_STOP_TERMS = {
    "一下",
    "什么",
    "告诉",
    "如何",
    "帮我",
    "怎么",
    "是否",
    "请问",
}
QUERY_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*|[\u3400-\u4dbf\u4e00-\u9fff]+", re.IGNORECASE)
CJK_TOKEN_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
CONVERSATION_CONTEXT_TERMS = (
    "attachment",
    "context",
    "document",
    "file",
    "knowledge",
    "notes",
    "provided",
    "uploaded",
    "上传",
    "上下文",
    "文档",
    "文件",
    "材料",
    "知识",
    "笔记",
    "资料",
    "附件",
)


@dataclass(frozen=True)
class Citation:
    document_id: str
    title: str
    chunk_index: int
    content: str
    source_type: str
    source_uri: str | None
    score: float
    retrieval_method: str
    evidence: dict[str, Any] | None = None

    def model_dump(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "document_title": self.title,
            "chunk_index": self.chunk_index,
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "score": self.score,
            "retrieval_method": self.retrieval_method,
            **(self.evidence or {}),
        }
        return {
            "document_id": self.document_id,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "score": self.score,
            "retrieval_method": self.retrieval_method,
            "metadata": metadata,
        }


class RagService:
    def __init__(self, session: AsyncSession, user_id: UUID | None = None) -> None:
        self.session = session
        self.user_id = user_id
        self.model_gateway = ModelGateway()

    async def index_text(self, payload: KnowledgeDocumentCreate) -> KnowledgeDocument:
        spans = self._split_spans(payload.content)
        if not spans:
            raise ValueError("Document must contain non-whitespace text")
        chunks = [content for content, _, _ in spans]
        contexts = self._chunk_contexts(payload.title, payload.content, spans)
        embeddings = await self._embed_or_empty(
            [f"{context}\n\n{chunk}" for context, chunk in zip(contexts, chunks, strict=True)]
        )
        document = KnowledgeDocument(
            conversation_id=payload.conversation_id,
            user_id=self.user_id if self.user_id is not None else payload.user_id,
            title=payload.title,
            source_type=payload.source_type,
            source_uri=payload.source_uri,
            status="indexed",
            metadata_={
                "chunker_version": "paragraph-v1",
                "content_sha256": hashlib.sha256(payload.content.encode("utf-8")).hexdigest(),
                "chunk_count": len(chunks),
                "embedded_chunks": len(embeddings),
                "retrieval_mode": "hybrid" if embeddings else "keyword",
                "embedding_model": self.model_gateway.settings.openai_embedding_model,
                "contextualization": "extractive-headings-v1",
            },
        )
        self.session.add(document)
        await self.session.flush()
        for index, (chunk, start, end) in enumerate(spans):
            self.session.add(
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk,
                    embedding=embeddings[index] if index < len(embeddings) else None,
                    metadata_={
                        "start_char": start,
                        "end_char": end,
                        "retrieval_context": contexts[index],
                    },
                )
            )
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def reindex_document(self, document_id: UUID) -> KnowledgeDocument | None:
        result = await self.session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id, KnowledgeDocument.user_id == self.user_id)
            .with_for_update()
        )
        document = result.scalar_one_or_none()
        if document is None:
            return None
        chunks = list(
            (
                await self.session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document_id)
                    .order_by(KnowledgeChunk.chunk_index)
                )
            ).all()
        )
        if not chunks:
            raise ValueError("Document has no indexed text")
        # Preserve original chunk IDs, bodies and citation offsets. Reconstruct
        # section lineage from stored chunks; source files need not be retained.
        combined, spans, offset = [], [], 0
        for chunk in chunks:
            combined.append(chunk.content)
            spans.append((chunk.content, offset, offset + len(chunk.content)))
            offset += len(chunk.content) + 2
        contexts = self._chunk_contexts(document.title, "\n\n".join(combined), spans)
        embeddings = await self._embed_or_empty(
            [
                f"{context}\n\n{chunk.content}"
                for context, chunk in zip(contexts, chunks, strict=True)
            ]
        )
        if not embeddings and any(chunk.embedding is not None for chunk in chunks):
            # Do not destroy a usable vector index during a provider outage.
            raise ValueError("Embedding service unavailable; existing index preserved")
        for index, chunk in enumerate(chunks):
            chunk.metadata_ = {**(chunk.metadata_ or {}), "retrieval_context": contexts[index]}
            chunk.embedding = embeddings[index] if embeddings else None
        document.metadata_ = {
            **(document.metadata_ or {}),
            "chunk_count": len(chunks),
            "embedded_chunks": len(embeddings),
            "retrieval_mode": "hybrid" if embeddings else "keyword",
            "embedding_model": self.model_gateway.settings.openai_embedding_model,
            "contextualization": "extractive-headings-v1",
        }
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def search(
        self, query: str, conversation_id: UUID | None = None, limit: int = 4
    ) -> list[Citation]:
        if not query.strip() or limit <= 0:
            return []
        limit = min(limit, MAX_RESULTS)
        candidates = min(MAX_CANDIDATES, max(24, limit * 6))
        # One AsyncSession cannot execute concurrent SQL operations. Both channels
        # run even when semantic retrieval succeeds; rank fusion needs both lists.
        keyword_results = await self._search_by_keywords(query, conversation_id, candidates)
        vector_results = await self._search_by_vector(query, conversation_id, candidates)
        if vector_results or keyword_results:
            fused = self._fuse(vector_results, keyword_results, candidates)
            return await self._rerank(query, fused, limit)
        if conversation_id is not None and self._requests_conversation_context(query):
            conversation_context = await self._conversation_document_context(conversation_id, limit)
            if conversation_context:
                return conversation_context
        return []

    async def _search_by_vector(
        self,
        query: str,
        conversation_id: UUID | None,
        limit: int,
        include_global: bool = True,
    ) -> list[Citation]:
        # Reserve time for lexical fallback inside the runtime retrieval budget.
        try:
            async with asyncio.timeout(
                min(4, self.model_gateway.settings.agent_retrieval_timeout_seconds / 2)
            ):
                embeddings = await self._embed_or_empty([query])
        except TimeoutError:
            embeddings = []
        if not embeddings:
            return []
        distance = KnowledgeChunk.embedding.cosine_distance(embeddings[0]).label("distance")
        statement = (
            select(KnowledgeChunk, KnowledgeDocument, distance)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeChunk.embedding.is_not(None))
            .where(distance <= 1 - MIN_VECTOR_SCORE)
            .order_by(distance, *self._retrieval_order(conversation_id), KnowledgeChunk.id)
            .limit(limit)
        )
        statement = self._scope_statement(statement, conversation_id, include_global)
        result = await self.session.execute(statement)
        citations = []
        for chunk, document, raw_distance in result.all():
            score = self._vector_score(raw_distance)
            if score < MIN_VECTOR_SCORE:
                continue
            citations.append(
                self._citation(
                    chunk,
                    document,
                    score=score,
                    retrieval_method="vector",
                )
            )
        return citations

    async def _search_by_keywords(
        self,
        query: str,
        conversation_id: UUID | None,
        limit: int,
        include_global: bool = True,
    ) -> list[Citation]:
        keywords = self._keyword_terms(query)
        if not keywords:
            return []
        matches = [
            func.lower(KnowledgeChunk.content).contains(term, autoescape=True) for term in keywords
        ]
        context = func.lower(
            func.coalesce(
                KnowledgeChunk.metadata_["retrieval_context"].astext, KnowledgeDocument.title
            )
        )
        context_matches = [context.contains(term, autoescape=True) for term in keywords]
        rank = sum(case((match, 1), else_=0) for match in matches) + 0.25 * sum(
            case((match, 1), else_=0) for match in context_matches
        )
        statement = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(or_(*matches, *context_matches))
            .order_by(rank.desc(), *self._retrieval_order(conversation_id), KnowledgeChunk.id)
            .limit(limit)
        )
        statement = self._scope_statement(statement, conversation_id, include_global)
        result = await self.session.execute(statement)
        scored: list[tuple[int, float, KnowledgeChunk, KnowledgeDocument]] = []
        for chunk, document in result.all():
            content = chunk.content.lower()
            context_text = (chunk.metadata_ or {}).get("retrieval_context", document.title).lower()
            score = sum(1 for keyword in keywords if keyword in content) + 0.25 * sum(
                1 for keyword in keywords if keyword in context_text
            )
            if score:
                scoped_priority = self._document_scope_priority(document, conversation_id)
                scored.append((scoped_priority, score, chunk, document))
        scored.sort(key=lambda item: (-item[1], item[0], str(item[2].id)))
        return [
            self._citation(
                chunk,
                document,
                score=float(score),
                retrieval_method="keyword",
            )
            for _, score, chunk, document in scored[:limit]
        ]

    def _keyword_terms(self, query: str) -> list[str]:
        """Extract bounded Latin tokens and CJK n-grams for offline retrieval.

        Whitespace tokenization works for English but turns a Chinese question into one
        long token, so even a clearly related document often fails the substring match.
        Character n-grams provide a deterministic fallback when embeddings are unavailable.
        """

        terms: list[str] = []
        seen: set[str] = set()

        def add(term: str) -> None:
            normalized = term.casefold()
            if normalized and normalized not in seen and len(terms) < MAX_KEYWORD_TERMS:
                seen.add(normalized)
                terms.append(normalized)

        for token in QUERY_TOKEN_RE.findall(query.casefold()):
            if CJK_TOKEN_RE.fullmatch(token):
                for size in (3, 2):
                    if len(token) < size:
                        continue
                    for start in range(len(token) - size + 1):
                        term = token[start : start + size]
                        if not any(stop_term in term for stop_term in CJK_KEYWORD_STOP_TERMS):
                            add(term)
                continue
            normalized_token = token.strip("._-")
            if len(normalized_token) >= 3 and normalized_token not in LATIN_KEYWORD_STOP_TERMS:
                add(normalized_token)
        return terms

    async def _conversation_document_context(
        self,
        conversation_id: UUID,
        limit: int,
    ) -> list[Citation]:
        statement = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .order_by(
                KnowledgeDocument.created_at.desc(),
                KnowledgeChunk.chunk_index.asc(),
                KnowledgeChunk.id,
            )
            .limit(limit)
        )
        result = await self.session.execute(
            self._scope_statement(statement, conversation_id, False)
        )
        return [
            self._citation(
                chunk,
                document,
                score=0.0,
                retrieval_method="conversation_context",
            )
            for chunk, document in result.all()
        ]

    def _scope_statement(self, statement, conversation_id: UUID | None, include_global: bool):
        # Anonymous/internal callers may only see legacy unowned documents.
        # A missing identity must never turn into a cross-account search.
        statement = statement.where(
            KnowledgeDocument.user_id == self.user_id,
            KnowledgeDocument.status == "indexed",
        )
        if conversation_id is None:
            return statement.where(KnowledgeDocument.conversation_id.is_(None))
        if not include_global:
            return statement.where(KnowledgeDocument.conversation_id == conversation_id)
        return statement.where(
            or_(
                KnowledgeDocument.conversation_id == conversation_id,
                KnowledgeDocument.conversation_id.is_(None),
            )
        )

    def _retrieval_order(self, conversation_id: UUID | None, *score_order):
        if conversation_id is None:
            return score_order
        return (
            case((KnowledgeDocument.conversation_id == conversation_id, 0), else_=1),
            *score_order,
        )

    def _document_scope_priority(
        self,
        document: KnowledgeDocument,
        conversation_id: UUID | None,
    ) -> int:
        if conversation_id is not None and document.conversation_id == conversation_id:
            return 0
        return 1

    def _citation(
        self,
        chunk: KnowledgeChunk,
        document: KnowledgeDocument,
        score: float,
        retrieval_method: str,
    ) -> Citation:
        return Citation(
            document_id=str(document.id),
            title=document.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            source_type=document.source_type,
            source_uri=document.source_uri,
            score=score,
            retrieval_method=retrieval_method,
            evidence={
                key: value
                for key, value in (chunk.metadata_ or {}).items()
                if key in {"start_char", "end_char", "retrieval_context"}
            },
        )

    def _vector_score(self, distance: float | None) -> float:
        if distance is None or not math.isfinite(float(distance)):
            return 0.0
        return max(0.0, 1.0 - float(distance))

    def _requests_conversation_context(self, query: str) -> bool:
        lowered = query.casefold()
        return any(term in lowered for term in CONVERSATION_CONTEXT_TERMS)

    async def _embed_or_empty(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = await self.model_gateway.embed_texts(texts)
            # The persisted pgvector column is vector(1536), independent of provider settings.
            if len(vectors) != len(texts) or any(len(vector) != 1536 for vector in vectors):
                return []
            return vectors
        except Exception:
            return []

    def _split_text(self, text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
        return [content for content, _, _ in self._split_spans(text, chunk_size, overlap)]

    def _split_spans(
        self, text: str, chunk_size: int = 900, overlap: int = 120
    ) -> list[tuple[str, int, int]]:
        if chunk_size <= 0 or not 0 <= overlap < chunk_size:
            raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
        # Offsets reference this normalized text; preserve paragraphs and code indentation.
        clean = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return []
        chunks: list[tuple[str, int, int]] = []
        start = 0
        while start < len(clean):
            end = min(start + chunk_size, len(clean))
            if end < len(clean):
                floor = start + max(chunk_size // 2, overlap + 1)
                for separator in ("\n\n", "\n", "。", ". ", " "):
                    boundary = clean.rfind(separator, floor, end)
                    if boundary >= floor:
                        end = boundary + len(separator)
                        break
            chunks.append((clean[start:end], start, end))
            if end == len(clean):
                break
            start = max(end - overlap, 0)
        return chunks

    def _fuse(self, vector: list[Citation], keyword: list[Citation], limit: int) -> list[Citation]:
        if not vector or not keyword:
            return (vector or keyword)[:limit]
        merged: dict[tuple[str, int], Citation] = {}
        scores: dict[tuple[str, int], float] = {}
        for channel, citations in (("vector", vector), ("keyword", keyword)):
            seen: set[tuple[str, int]] = set()
            previous_score, rank = None, 0
            for position, citation in enumerate(citations, 1):
                # Equal similarities must not acquire different relevance merely
                # because UUID ordering broke a database tie.
                if previous_score is None or not math.isclose(
                    citation.score, previous_score, rel_tol=1e-6, abs_tol=1e-8
                ):
                    rank = position
                previous_score = citation.score
                key = (citation.document_id, citation.chunk_index)
                if key in seen:
                    continue
                seen.add(key)
                existing = merged.get(key, citation)
                evidence = {
                    **(existing.evidence or {}),
                    f"{channel}_rank": rank,
                    f"{channel}_score": citation.score,
                }
                scores[key] = scores.get(key, 0.0) + 1 / (RRF_K + rank)
                merged[key] = replace(
                    existing, score=scores[key], retrieval_method="hybrid_rrf", evidence=evidence
                )
        # Prefer lexical evidence for equal fusion scores, useful for exact IDs.
        ordered = sorted(
            merged,
            key=lambda key: (
                -scores[key],
                (merged[key].evidence or {}).get("keyword_rank", MAX_CANDIDATES + 1),
                key,
            ),
        )
        return [merged[key] for key in ordered[:limit]]

    def _chunk_contexts(
        self, title: str, text: str, spans: list[tuple[str, int, int]]
    ) -> list[str]:
        """Extractive Contextual Retrieval: retain title and section lineage.

        Context only enriches embedding/lexical/reranker inputs. Citation bodies
        stay verbatim. This variant needs no per-chunk generative model calls.
        """
        clean = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        headings = list(re.finditer(r"^(#{1,6})[ \t]+([^\n]+)$", clean, re.MULTILINE))
        contexts, stack = [], []
        cursor = 0
        for _, start, end in spans:
            while cursor < len(headings) and headings[cursor].start() <= start:
                heading = headings[cursor]
                level = len(heading.group(1))
                stack = [(depth, label) for depth, label in stack if depth < level]
                stack.append((level, heading.group(2).strip()[:120]))
                cursor += 1
            labels = [title[:260], *(label for _, label in stack)]
            # A chunk crossing a section boundary also carries the new heading.
            lookahead = cursor
            while lookahead < len(headings) and headings[lookahead].start() < end:
                labels.append(headings[lookahead].group(2).strip()[:120])
                lookahead += 1
            contexts.append(" > ".join(dict.fromkeys(labels))[:700])
        return contexts

    async def _rerank(self, query: str, candidates: list[Citation], limit: int) -> list[Citation]:
        settings = self.model_gateway.settings
        pool = candidates[: settings.rag_rerank_candidates]
        texts = [
            f"{(item.evidence or {}).get('retrieval_context', item.title)}\n\n{item.content}"
            for item in pool
        ]
        ranked = await RetrievalReranker(settings).rank(query, texts)
        if ranked is not None:
            pool = [
                replace(
                    pool[index],
                    evidence={
                        **(pool[index].evidence or {}),
                        "rerank_score": score,
                        "reranker": settings.rag_rerank_model,
                    },
                )
                for index, score in ranked
            ]
        selected = diverse_indices(
            [item.content for item in pool],
            limit,
            [str((item.evidence or {}).get("retrieval_context", item.title)) for item in pool],
        )
        return [
            replace(
                pool[index],
                evidence={
                    **(pool[index].evidence or {}),
                    "selection_method": "mmr",
                    "rerank_status": "applied"
                    if ranked is not None
                    else "fallback"
                    if settings.rag_rerank_mode == "api" and len(pool) >= 2
                    else "local",
                },
            )
            for index in selected
        ]

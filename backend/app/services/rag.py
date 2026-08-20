from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.schemas import KnowledgeDocumentCreate
from app.services.model_gateway import ModelGateway


MIN_VECTOR_SCORE = 0.45
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

    def model_dump(self) -> dict[str, str | int | float | dict[str, str | int | float]]:
        metadata: dict[str, str | int | float] = {
            "document_title": self.title,
            "chunk_index": self.chunk_index,
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "score": self.score,
            "retrieval_method": self.retrieval_method,
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
        document = KnowledgeDocument(
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or self.user_id,
            title=payload.title,
            source_type=payload.source_type,
            source_uri=payload.source_uri,
            status="indexed",
        )
        self.session.add(document)
        await self.session.flush()
        chunks = self._split_text(payload.content)
        embeddings = await self._embed_or_empty(chunks)
        for index, chunk in enumerate(chunks):
            self.session.add(
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk,
                    embedding=embeddings[index] if index < len(embeddings) else None,
                    metadata_={},
                )
            )
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def search(self, query: str, conversation_id: UUID | None = None, limit: int = 4) -> list[Citation]:
        vector_results = await self._search_by_vector(query, conversation_id, limit)
        if vector_results:
            return vector_results
        keyword_results = await self._search_by_keywords(query, conversation_id, limit)
        if keyword_results:
            return keyword_results
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
        embeddings = await self._embed_or_empty([query])
        if not embeddings:
            return []
        distance = KnowledgeChunk.embedding.cosine_distance(embeddings[0]).label("distance")
        statement = (
            select(KnowledgeChunk, KnowledgeDocument, distance)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeChunk.embedding.is_not(None))
            .order_by(*self._retrieval_order(conversation_id, distance))
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
        keywords = [part.lower() for part in query.split() if len(part) > 2]
        if not keywords:
            return []
        statement = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .order_by(*self._retrieval_order(conversation_id))
            .limit(50)
        )
        statement = self._scope_statement(statement, conversation_id, include_global)
        result = await self.session.execute(statement)
        scored: list[tuple[int, int, KnowledgeChunk, KnowledgeDocument]] = []
        for chunk, document in result.all():
            content = chunk.content.lower()
            score = sum(1 for keyword in keywords if keyword in content)
            if score:
                scoped_priority = self._document_scope_priority(document, conversation_id)
                scored.append((scoped_priority, score, chunk, document))
        scored.sort(key=lambda item: (item[0], -item[1], item[3].created_at, item[2].chunk_index))
        return [
            self._citation(
                chunk,
                document,
                score=float(score),
                retrieval_method="keyword",
            )
            for _, score, chunk, document in scored[:limit]
        ]

    async def _conversation_document_context(
        self,
        conversation_id: UUID,
        limit: int,
    ) -> list[Citation]:
        result = await self.session.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.conversation_id == conversation_id)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeChunk.chunk_index.asc())
            .limit(limit)
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
        if self.user_id is not None:
            statement = statement.where(KnowledgeDocument.user_id == self.user_id)
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
        )

    def _vector_score(self, distance: float | None) -> float:
        if distance is None:
            return 0.0
        return max(0.0, 1.0 - float(distance))

    def _requests_conversation_context(self, query: str) -> bool:
        lowered = query.casefold()
        return any(term in lowered for term in CONVERSATION_CONTEXT_TERMS)

    async def _embed_or_empty(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self.model_gateway.embed_texts(texts)
        except Exception:
            return []

    def _split_text(self, text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
        clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not clean:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(clean):
            end = min(start + chunk_size, len(clean))
            chunks.append(clean[start:end])
            if end == len(clean):
                break
            start = max(end - overlap, 0)
        return chunks

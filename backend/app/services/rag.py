from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.schemas import KnowledgeDocumentCreate
from app.services.model_gateway import ModelGateway


@dataclass(frozen=True)
class Citation:
    document_id: str
    title: str
    chunk_index: int
    content: str

    def model_dump(self) -> dict[str, str | int]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "content": self.content,
        }


class RagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.model_gateway = ModelGateway()

    async def index_text(self, payload: KnowledgeDocumentCreate) -> KnowledgeDocument:
        document = KnowledgeDocument(
            conversation_id=payload.conversation_id,
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
        if conversation_id is not None:
            vector_results = await self._search_by_vector(query, conversation_id, limit, include_global=False)
            if vector_results:
                return vector_results
            keyword_results = await self._search_by_keywords(query, conversation_id, limit, include_global=False)
            if keyword_results:
                return keyword_results
            conversation_context = await self._conversation_document_context(conversation_id, limit)
            if conversation_context:
                return conversation_context

        vector_results = await self._search_by_vector(query, None, limit)
        if vector_results:
            return vector_results
        keyword_results = await self._search_by_keywords(query, None, limit)
        if keyword_results:
            return keyword_results
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
        distance = KnowledgeChunk.embedding.cosine_distance(embeddings[0])
        statement = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        statement = self._scope_statement(statement, conversation_id, include_global)
        result = await self.session.execute(statement)
        return [
            Citation(
                document_id=str(document.id),
                title=document.title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
            )
            for chunk, document in result.all()
        ]

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
            .limit(50)
        )
        statement = self._scope_statement(statement, conversation_id, include_global)
        result = await self.session.execute(statement)
        scored: list[tuple[int, KnowledgeChunk, KnowledgeDocument]] = []
        for chunk, document in result.all():
            content = chunk.content.lower()
            score = sum(1 for keyword in keywords if keyword in content)
            if score:
                scored.append((score, chunk, document))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            Citation(
                document_id=str(document.id),
                title=document.title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
            )
            for _, chunk, document in scored[:limit]
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
            Citation(
                document_id=str(document.id),
                title=document.title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
            )
            for chunk, document in result.all()
        ]

    def _scope_statement(self, statement, conversation_id: UUID | None, include_global: bool):
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

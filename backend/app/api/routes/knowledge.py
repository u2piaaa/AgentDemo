from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import CurrentUser
from app.db.database import get_session
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeDocument
from app.schemas import KnowledgeDocumentCreate, KnowledgeDocumentRead, McpResourceImportRequest
from app.services.rag import RagService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/documents", response_model=list[KnowledgeDocumentRead])
async def list_documents(
    current_user: CurrentUser,
    conversation_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDocument]:
    statement = select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())
    if conversation_id is None:
        return []
    else:
        await require_owned_conversation(session, conversation_id, current_user.id)
        statement = statement.where(KnowledgeDocument.conversation_id == conversation_id)
    result = await session.execute(statement)
    return list(result.scalars().all())


@router.post("/documents", response_model=KnowledgeDocumentRead)
async def create_document(
    payload: KnowledgeDocumentCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocumentRead:
    if payload.conversation_id is not None:
        await require_owned_conversation(session, payload.conversation_id, current_user.id)
    service = RagService(session, user_id=current_user.id)
    return await service.index_text(payload.model_copy(update={"user_id": current_user.id}))


@router.post("/mcp-resource", response_model=KnowledgeDocumentRead)
async def import_mcp_resource(
    payload: McpResourceImportRequest,
    current_user: CurrentUser,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocumentRead:
    if payload.conversation_id is not None:
        await require_owned_conversation(session, payload.conversation_id, current_user.id)
    resource = await request.app.state.mcp_client.read_resource(payload.server_name, payload.uri)
    content = extract_mcp_resource_text(resource)
    if not content.strip():
        raise HTTPException(status_code=422, detail="MCP resource did not contain text")
    document = KnowledgeDocumentCreate(
        title=payload.title or str(resource.get("name") or payload.uri),
        source_type="mcp_resource",
        source_uri=payload.uri,
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        content=content,
    )
    return await RagService(session, user_id=current_user.id).index_text(document)


def extract_mcp_resource_text(resource: dict) -> str:
    """Normalize inline and standard MCP resources/read content blocks to text."""

    collected: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in collected:
                collected.append(text)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if isinstance(value, dict):
            for key in ("text", "content"):
                collect(value.get(key))

    collect(resource.get("text"))
    collect(resource.get("content"))
    collect(resource.get("contents"))
    return "\n\n".join(collected)


@router.post("/documents/upload", response_model=KnowledgeDocumentRead)
async def upload_document(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    conversation_id: UUID | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocumentRead:
    if conversation_id is not None:
        await require_owned_conversation(session, conversation_id, current_user.id)
    raw = await file.read()
    content = extract_text(file.filename or "uploaded-document", raw, file.content_type)
    if not content.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from the document")
    payload = KnowledgeDocumentCreate(
        title=file.filename or "uploaded-document",
        source_type=detect_source_type(file.filename or "", file.content_type),
        source_uri=f"upload:{file.filename}",
        user_id=current_user.id,
        conversation_id=conversation_id,
        content=content,
    )
    return await RagService(session, user_id=current_user.id).index_text(payload)


async def require_owned_conversation(
    session: AsyncSession,
    conversation_id: UUID,
    user_id: UUID,
) -> None:
    result = await session.execute(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")


def detect_source_type(filename: str, content_type: str | None) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf") or content_type == "application/pdf":
        return "pdf"
    if lower.endswith(".md"):
        return "markdown"
    return "text"


def extract_text(filename: str, raw: bytes, content_type: str | None) -> str:
    source_type = detect_source_type(filename, content_type)
    if source_type == "pdf":
        reader = PdfReader(BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if source_type in {"markdown", "text"} or filename.lower().endswith((".txt", ".md")):
        return raw.decode("utf-8-sig", errors="replace")
    raise HTTPException(
        status_code=415,
        detail="Unsupported document type. Upload TXT, Markdown, or text-based PDF files.",
    )

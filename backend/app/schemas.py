from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


USERNAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{2,39}$"


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=4, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username_input(cls, value: str) -> str:
        return value.strip()


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str


class AuthResponse(BaseModel):
    token: str
    user: UserRead


class ConversationCreate(BaseModel):
    title: str = "New conversation"


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    conversation_id: UUID | None = None
    message: str = Field(min_length=1)
    task_type: str = "conversation"


class ChatEvent(BaseModel):
    type: str
    data: dict[str, Any]


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime
    metadata_: dict[str, Any] = Field(serialization_alias="metadata")


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID | None
    name: str
    status: str
    progress: int
    error: str | None
    result: dict[str, Any] | None
    metadata_: dict[str, Any] = Field(serialization_alias="metadata")
    created_at: datetime


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    conversation_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    status: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    error: str | None = None
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ToolManifestRead(BaseModel):
    name: str
    description: str
    permission: str
    enabled: bool
    parameters: dict[str, Any]


class ToolRunRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    tool_name: str
    duration_ms: int
    output: Any


class KnowledgeDocumentCreate(BaseModel):
    title: str
    source_type: str = "text"
    source_uri: str | None = None
    conversation_id: UUID | None = None
    content: str


class KnowledgeDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID | None
    title: str
    source_type: str
    status: str
    created_at: datetime

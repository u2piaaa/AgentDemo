from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.task import TASK_STATUSES


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


class ToolConfirmationRequest(BaseModel):
    conversation_id: UUID
    message: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = "Confirmed by the user."
    task_type: str = "conversation"


class ChatEvent(BaseModel):
    type: str
    data: dict[str, Any]


class AgentToolPlan(BaseModel):
    no_tool: bool = True
    tool_name: str | None = None
    provider: str = "local_plugin"
    provider_tool_id: str | None = None
    server_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    requires_confirmation: bool = False


class AgentExecutionState(BaseModel):
    user_id: UUID | None = None
    task_id: UUID | None = None
    conversation_id: UUID | None = None
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    memory_summaries: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    mcp_resources: list[dict[str, Any]] = Field(default_factory=list)
    mcp_prompts: list[dict[str, Any]] = Field(default_factory=list)
    plan: AgentToolPlan = Field(default_factory=AgentToolPlan)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    final_answer: str = ""
    trace_id: str = Field(default_factory=lambda: uuid4().hex)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime
    metadata_: dict[str, Any] = Field(serialization_alias="metadata")


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    conversation_id: UUID | None
    schedule_id: UUID | None = None
    name: str
    kind: str = "manual"
    input_: dict[str, Any] = Field(default_factory=dict, serialization_alias="input")
    status: str
    progress: int
    error: str | None
    result: dict[str, Any] | None
    trace_id: str | None
    idempotency_key: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    metadata_: dict[str, Any] = Field(serialization_alias="metadata")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    conversation_id: UUID | None = None
    trace_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTaskCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    conversation_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    max_attempts: int | None = Field(default=None, ge=1, le=10)

    @field_validator("prompt")
    @classmethod
    def validate_agent_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must include non-whitespace text")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < 8:
            raise ValueError("idempotency_key must contain at least 8 non-whitespace characters")
        return value


class TaskScheduleCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    conversation_id: UUID | None = None
    schedule_kind: Literal["once", "interval", "daily"]
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    run_at: datetime | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)
    daily_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    max_attempts: int | None = Field(default=None, ge=1, le=10)

    @field_validator("prompt")
    @classmethod
    def validate_schedule_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must include non-whitespace text")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("timezone must not be empty")
        return value

    @model_validator(mode="after")
    def validate_schedule_options(self) -> "TaskScheduleCreate":
        if self.schedule_kind == "once" and self.run_at is None:
            raise ValueError("run_at is required for a one-time schedule")
        if self.schedule_kind == "interval" and self.interval_minutes is None:
            raise ValueError("interval_minutes is required for an interval schedule")
        if self.schedule_kind == "daily" and self.daily_time is None:
            raise ValueError("daily_time is required for a daily schedule")
        return self


class TaskScheduleUpdate(BaseModel):
    enabled: bool


class TaskScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID | None
    name: str
    prompt: str
    schedule_kind: str
    timezone: str
    run_at: datetime | None
    interval_seconds: int | None
    daily_time: str | None
    max_attempts: int
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_task_id: UUID | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TaskUpdate(BaseModel):
    status: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    error: str | None = None
    result: dict[str, Any] | None = None
    trace_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in TASK_STATUSES:
            allowed = ", ".join(sorted(TASK_STATUSES))
            raise ValueError(f"status must be one of: {allowed}")
        return value


class ToolManifestRead(BaseModel):
    name: str
    description: str
    permission: str
    provider: str = "local_plugin"
    provider_tool_id: str | None = None
    transport: str = "python"
    server_name: str | None = None
    requires_confirmation: bool = False
    enabled: bool
    parameters: dict[str, Any]
    timeout_seconds: int
    output_strategy: dict[str, Any] = Field(default_factory=dict)


class ToolRunRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class ToolRunResponse(BaseModel):
    tool_name: str
    provider: str = "local_plugin"
    provider_tool_id: str | None = None
    server_name: str | None = None
    status: str = "success"
    output: Any = None
    output_summary: str | None = None
    error: str | None = None
    duration_ms: int = 0
    trace_id: str = Field(default_factory=lambda: uuid4().hex)


class KnowledgeDocumentCreate(BaseModel):
    title: str
    source_type: str = "text"
    source_uri: str | None = None
    user_id: UUID | None = None
    conversation_id: UUID | None = None
    content: str


class McpResourceImportRequest(BaseModel):
    server_name: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    title: str | None = None
    conversation_id: UUID | None = None


class KnowledgeDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID | None
    title: str
    source_type: str
    status: str
    created_at: datetime


class CitationMetadata(BaseModel):
    document_title: str
    chunk_index: int
    source_type: str
    source_uri: str | None = None
    score: float
    retrieval_method: str


class CitationRead(BaseModel):
    document_id: UUID
    title: str
    chunk_index: int
    content: str
    source_type: str
    source_uri: str | None = None
    score: float
    retrieval_method: str
    metadata: CitationMetadata


class MemorySummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    summary: str
    valid_from: datetime | None
    valid_to: datetime | None
    disabled: bool
    created_at: datetime
    updated_at: datetime

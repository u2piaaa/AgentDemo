from typing import Any, TypedDict
from uuid import UUID

from app.schemas import AgentToolPlan
from app.services.model_gateway import ModelRoute


class AgentGraphState(TypedDict, total=False):
    user_id: UUID | None
    task_id: UUID | None
    conversation_id: UUID | None
    message: str
    task_type: str
    history: list[dict[str, str]]
    memory_summaries: list[str]
    citations: list[dict[str, Any]]
    mcp_resources: list[dict[str, Any]]
    mcp_prompts: list[dict[str, Any]]
    plan: AgentToolPlan
    tool_calls: list[dict[str, Any]]
    observations: list[str]
    final_answer: str
    trace_id: str
    route: ModelRoute
    tool_rounds: int
    max_tool_rounds: int
    save_user_message: bool
    confirmed_tool_name: str | None
    confirmed_arguments: dict[str, Any]
    confirmed_reason: str
    events: list[dict[str, str]]
    conversation: Any

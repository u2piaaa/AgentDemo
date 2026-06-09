import json
from dataclasses import asdict
from uuid import UUID

from langgraph.config import get_stream_writer

from app.agent.state import AgentGraphState
from app.schemas import AgentExecutionState


def runtime_event(event_type: str, data: dict) -> dict[str, str]:
    return {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}


def emit_event(event_type: str, data: dict) -> None:
    get_stream_writer()(runtime_event(event_type, data))


def emit_runtime_event(event: dict[str, str]) -> None:
    get_stream_writer()(event)


def emit_status(label: str, state: AgentGraphState, **extra: object) -> None:
    payload = {"label": label, "trace_id": state.get("trace_id"), **extra}
    emit_event("status", payload)


def done_event(conversation_id: UUID, state: AgentExecutionState, route) -> dict[str, str]:
    return runtime_event(
        "done",
        {
            "conversation_id": str(conversation_id),
            "citations": state.citations,
            "mcp_resources": state.mcp_resources,
            "mcp_prompts": state.mcp_prompts,
            "tool_calls": state.tool_calls,
            "trace_id": state.trace_id,
            "model_route": asdict(route),
        },
    )

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runtime import AgentRuntime
from app.models.task import (
    TASK_KIND_AGENT,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    Task,
)
from app.schemas import ChatRequest

PERSISTED_EVENT_TYPES = {"status", "plan", "tool_call", "tool_result", "done", "error"}
MAX_TASK_EVENTS = 100
CONFIRMATION_REQUIRED_ERROR = "Tool requires confirmation before execution"

STATUS_PROGRESS = {
    "ensure_conversation": 5,
    "load_history": 10,
    "save_user_message": 15,
    "retrieving_context": 25,
    "planning": 35,
    "generating": 70,
    "save_assistant_message": 90,
}
EVENT_PROGRESS = {
    "plan": 40,
    "tool_call": 50,
    "tool_result": 65,
    "done": 100,
}


class AgentTaskRunner:
    """Execute one durable background agent task and persist coarse-grained events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tool_registry,
        runtime_factory=AgentRuntime,
    ) -> None:
        self.session_factory = session_factory
        self.tool_registry = tool_registry
        self.runtime_factory = runtime_factory

    async def run(self, task_id: UUID) -> None:
        async with self.session_factory() as task_session:
            task = await task_session.get(Task, task_id)
            if task is None or task.kind != TASK_KIND_AGENT or task.status != TASK_STATUS_QUEUED:
                return

            prompt = str((task.input_ or {}).get("prompt") or "").strip()
            if not prompt:
                await self._finish_failed(
                    task_session, task, "Agent task input did not include a prompt"
                )
                return

            task.status = TASK_STATUS_RUNNING
            task.progress = max(task.progress or 0, 1)
            task.error = None
            task.started_at = datetime.now(UTC)
            task.finished_at = None
            await task_session.commit()

            answer_parts: list[str] = []
            done_payload: dict[str, Any] | None = None
            failure: str | None = None

            try:
                async with self.session_factory() as runtime_session:
                    runtime = self.runtime_factory(
                        session=runtime_session,
                        plugin_registry=self.tool_registry,
                        user_id=task.user_id,
                        task_id=task.id,
                    )
                    async for event in runtime.stream(
                        ChatRequest(
                            conversation_id=task.conversation_id,
                            message=prompt,
                            task_type="background",
                        )
                    ):
                        event_type = str(event.get("event") or "")
                        data = self._event_data(event)
                        if event_type == "token":
                            answer_parts.append(str(data.get("text") or ""))
                        elif event_type == "done":
                            done_payload = data
                        elif event_type == "error":
                            failure = str(
                                data.get("message") or "Background agent task failed"
                            )
                        elif (
                            event_type == "tool_result"
                            and data.get("error") == CONFIRMATION_REQUIRED_ERROR
                        ):
                            failure = (
                                "This background task needs tool confirmation. "
                                "Run the request in interactive chat to review and confirm the tool call."
                            )
                        if event_type in PERSISTED_EVENT_TYPES:
                            await self._record_event(
                                task_session, task, event_type, data
                            )
            except asyncio.CancelledError:
                task.status = TASK_STATUS_CANCELLED
                task.finished_at = datetime.now(UTC)
                await task_session.commit()
                raise
            except Exception as exc:
                await self._finish_failed(task_session, task, str(exc))
                return

            # Cancellation is written through a separate request session, so refresh
            # before committing a terminal result to avoid overwriting it in a race.
            await task_session.refresh(task)
            if task.status == TASK_STATUS_CANCELLED:
                return
            if failure is not None:
                await self._finish_failed(task_session, task, failure)
                return
            if done_payload is None:
                await self._finish_failed(
                    task_session, task, "Agent task ended without a completion event"
                )
                return

            conversation_id = done_payload.get("conversation_id")
            if conversation_id:
                task.conversation_id = UUID(str(conversation_id))
            task.status = TASK_STATUS_SUCCEEDED
            task.progress = 100
            task.trace_id = str(done_payload.get("trace_id") or task.trace_id or "") or None
            task.result = {
                "answer": "".join(answer_parts),
                "conversation_id": str(task.conversation_id) if task.conversation_id else None,
                "citations": done_payload.get("citations") or [],
                "tool_calls": done_payload.get("tool_calls") or [],
                "model_route": done_payload.get("model_route"),
            }
            task.finished_at = datetime.now(UTC)
            await task_session.commit()

    async def _record_event(
        self,
        session: AsyncSession,
        task: Task,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        metadata = dict(task.metadata_ or {})
        events = list(metadata.get("events") or [])
        events.append(
            {
                "type": event_type,
                "at": datetime.now(UTC).isoformat(),
                **self._compact_event_data(data),
            }
        )
        metadata["events"] = events[-MAX_TASK_EVENTS:]
        task.metadata_ = metadata
        task.progress = max(task.progress or 0, self._progress(event_type, data))
        trace_id = data.get("trace_id")
        if trace_id:
            task.trace_id = str(trace_id)
        await session.commit()

    async def _finish_failed(self, session: AsyncSession, task: Task, error: str) -> None:
        task.status = TASK_STATUS_FAILED
        task.error = error or "Background agent task failed"
        task.finished_at = datetime.now(UTC)
        await session.commit()

    def _event_data(self, event: dict[str, str]) -> dict[str, Any]:
        raw = event.get("data") or "{}"
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {"message": str(raw)}
        return value if isinstance(value, dict) else {"value": value}

    def _compact_event_data(self, data: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "label",
            "model",
            "tool_name",
            "provider",
            "provider_tool_id",
            "server_name",
            "status",
            "reason",
            "error",
            "message",
            "trace_id",
        )
        return {key: data[key] for key in keys if data.get(key) is not None}

    def _progress(self, event_type: str, data: dict[str, Any]) -> int:
        if event_type == "status":
            return STATUS_PROGRESS.get(str(data.get("label") or ""), 1)
        return EVENT_PROGRESS.get(event_type, 1)

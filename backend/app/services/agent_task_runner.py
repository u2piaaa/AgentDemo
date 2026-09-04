import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runtime import AgentRuntime
from app.core.config import get_settings
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
INTERRUPTED_TASK_ERROR = "Execution interrupted; task safely returned to the queue"

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
NON_RETRYABLE_ERROR_MARKERS = (
    "authentication",
    "confirmation",
    "forbidden",
    "invalid api key",
    "invalid input",
    "missing api key",
    "not include a prompt",
    "not available",
    "outside workspace",
    "permission denied",
    "requires confirmation",
    "unauthorized",
)
RETRYABLE_ERROR_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "network",
    "rate limit",
    "temporarily",
    "timeout",
    "timed out",
    "unavailable",
)


@dataclass(frozen=True)
class TaskRunOutcome:
    retry_at: datetime | None = None


class AgentTaskRunner:
    """Execute one durable background agent task and persist coarse-grained events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tool_registry,
        runtime_factory=AgentRuntime,
        *,
        retry_base_seconds: int | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.session_factory = session_factory
        self.tool_registry = tool_registry
        self.runtime_factory = runtime_factory
        self.retry_base_seconds = max(
            retry_base_seconds or settings.agent_task_retry_base_seconds, 1
        )
        self.lease_seconds = max(lease_seconds or settings.agent_task_lease_seconds, 5)
        self.heartbeat_seconds = max(
            heartbeat_seconds or settings.agent_task_heartbeat_seconds, 1
        )

    async def run(self, task_id: UUID) -> TaskRunOutcome:
        async with self.session_factory() as task_session:
            # Serialize the queued -> running claim across scheduler processes.
            # A competing worker skips the locked row and returns without running it.
            task = await task_session.get(
                Task,
                task_id,
                with_for_update={"skip_locked": True},
            )
            if task is None or task.kind != TASK_KIND_AGENT or task.status != TASK_STATUS_QUEUED:
                return TaskRunOutcome()

            prompt = str((task.input_ or {}).get("prompt") or "").strip()
            if not prompt:
                await self._finish_failed(
                    task_session, task, "Agent task input did not include a prompt"
                )
                return TaskRunOutcome()

            now = datetime.now(UTC)
            next_attempt_at = getattr(task, "next_attempt_at", None)
            if next_attempt_at is not None and next_attempt_at > now:
                return TaskRunOutcome(retry_at=next_attempt_at)
            task.status = TASK_STATUS_RUNNING
            task.progress = max(task.progress or 0, 1)
            task.error = None
            task.attempt_count = (getattr(task, "attempt_count", 0) or 0) + 1
            task.started_at = task.started_at or now
            task.finished_at = None
            task.next_attempt_at = None
            task.heartbeat_at = now
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            await task_session.commit()

            answer_parts: list[str] = []
            done_payload: dict[str, Any] | None = None
            failure: str | None = None

            heartbeat_job = asyncio.create_task(self._heartbeat_loop(task.id))
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
                # The cancellation endpoint persists `cancelled` before stopping
                # the worker. Other cancellations (notably service shutdown) must
                # remain resumable and must not consume a retry attempt.
                await task_session.refresh(task)
                if task.status != TASK_STATUS_CANCELLED:
                    task.status = TASK_STATUS_QUEUED
                    task.error = INTERRUPTED_TASK_ERROR
                    task.attempt_count = max((task.attempt_count or 1) - 1, 0)
                    task.finished_at = None
                    task.next_attempt_at = datetime.now(UTC)
                task.lease_expires_at = None
                await task_session.commit()
                raise
            except Exception as exc:
                return await self._finish_or_retry(
                    task_session,
                    task,
                    str(exc),
                    retryable=self._is_retryable_error(str(exc)),
                )
            finally:
                heartbeat_job.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_job

            # Cancellation is written through a separate request session, so refresh
            # before committing a terminal result to avoid overwriting it in a race.
            await task_session.refresh(task)
            if task.status == TASK_STATUS_CANCELLED:
                return TaskRunOutcome()
            if failure is not None:
                return await self._finish_or_retry(
                    task_session,
                    task,
                    failure,
                    retryable=self._is_retryable_error(failure),
                )
            if done_payload is None:
                return await self._finish_or_retry(
                    task_session,
                    task,
                    "Agent task ended without a completion event",
                    retryable=True,
                )

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
            task.next_attempt_at = None
            task.heartbeat_at = datetime.now(UTC)
            task.lease_expires_at = None
            await task_session.commit()
            return TaskRunOutcome()

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
        now = datetime.now(UTC)
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        await session.commit()

    async def _finish_failed(self, session: AsyncSession, task: Task, error: str) -> None:
        task.status = TASK_STATUS_FAILED
        task.error = error or "Background agent task failed"
        task.finished_at = datetime.now(UTC)
        task.next_attempt_at = None
        task.lease_expires_at = None
        await session.commit()

    async def _finish_or_retry(
        self,
        session: AsyncSession,
        task: Task,
        error: str,
        *,
        retryable: bool,
    ) -> TaskRunOutcome:
        attempts = getattr(task, "attempt_count", 0) or 0
        max_attempts = max(getattr(task, "max_attempts", 1) or 1, 1)
        if retryable and attempts < max_attempts:
            delay_seconds = min(self.retry_base_seconds * (2 ** max(attempts - 1, 0)), 3600)
            retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            task.status = TASK_STATUS_QUEUED
            task.error = f"Attempt {attempts}/{max_attempts} failed; retry scheduled: {error}"
            task.finished_at = None
            task.next_attempt_at = retry_at
            task.lease_expires_at = None
            metadata = dict(task.metadata_ or {})
            events = list(metadata.get("events") or [])
            events.append(
                {
                    "type": "retry_scheduled",
                    "at": datetime.now(UTC).isoformat(),
                    "attempt": attempts,
                    "max_attempts": max_attempts,
                    "retry_at": retry_at.isoformat(),
                    "error": error,
                }
            )
            metadata["events"] = events[-MAX_TASK_EVENTS:]
            task.metadata_ = metadata
            await session.commit()
            return TaskRunOutcome(retry_at=retry_at)
        await self._finish_failed(session, task, error)
        return TaskRunOutcome()

    async def _heartbeat_loop(self, task_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            async with self.session_factory() as session:
                task = await session.get(Task, task_id)
                if task is None or task.status != TASK_STATUS_RUNNING:
                    return
                now = datetime.now(UTC)
                task.heartbeat_at = now
                task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                await session.commit()

    def _is_retryable_error(self, error: str) -> bool:
        lowered = error.casefold()
        if any(marker in lowered for marker in NON_RETRYABLE_ERROR_MARKERS):
            return False
        if any(marker in lowered for marker in RETRYABLE_ERROR_MARKERS):
            return True
        # Unknown provider or infrastructure failures are retried within the
        # bounded attempt budget; known user/configuration errors fail above.
        return True

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

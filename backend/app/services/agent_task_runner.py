import asyncio
import json
from contextlib import aclosing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from random import uniform
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runtime import AgentRuntime
from app.core.config import get_settings
from app.models.task import (
    TASK_KIND_AGENT,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    Task,
)
from app.schemas import ChatRequest
from app.models.conversation import Conversation
from app.services.task_lease import (
    TaskLease, TaskLeaseLost, current_task_lease, lock_task_lease, REPLAY_BLOCKED_ERROR,
)

logger = logging.getLogger(__name__)

PERSISTED_EVENT_TYPES = {"status", "plan", "tool_call", "tool_result", "done", "error"}
MAX_TASK_EVENTS = 100
CONFIRMATION_REQUIRED_ERROR = "Tool requires confirmation before execution"
INTERRUPTED_TASK_ERROR = "Execution interrupted before external work; retry scheduled within the attempt budget"

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
        async with self.session_factory() as session:
            task = await session.get(Task, task_id, with_for_update={"skip_locked": True})
            if task is None or task.kind != TASK_KIND_AGENT or task.status != TASK_STATUS_QUEUED:
                return TaskRunOutcome()
            now = datetime.now(UTC)
            prompt = str((task.input_ or {}).get("prompt") or "").strip()
            error = None
            if not prompt:
                error = "Agent task input did not include a prompt"
            elif task.deadline_at is not None and task.deadline_at <= now:
                error = "Task deadline expired before execution"
            elif task.attempt_count >= task.max_attempts:
                error = "Task attempt budget exhausted"
            elif task.replay_blocked_at is not None:
                error = REPLAY_BLOCKED_ERROR
            if error:
                await self._finish_failed(session, task, error)
                return TaskRunOutcome()
            if task.next_attempt_at is not None and task.next_attempt_at > now:
                return TaskRunOutcome(retry_at=task.next_attempt_at)
            # Persist the conversation before runtime starts, so retries reuse it.
            if task.conversation_id is None:
                conversation = Conversation(id=uuid4(), user_id=task.user_id, title="New conversation")
                session.add(conversation)
                await session.flush()
                task.conversation_id = conversation.id
            task.status = TASK_STATUS_RUNNING
            task.run_token = uuid4()
            task.progress = 1
            task.error = None
            task.attempt_count += 1
            task.started_at = task.started_at or now
            task.finished_at = None
            task.next_attempt_at = None
            task.deadline_at = task.deadline_at or now + timedelta(seconds=900)
            task.heartbeat_at = now
            task.lease_expires_at = min(now + timedelta(seconds=self.lease_seconds), task.deadline_at)
            await session.commit()
            lease = TaskLease(task.id, task.run_token)
            conversation_id, user_id = task.conversation_id, task.user_id
            timeout = min(task.timeout_seconds, (task.deadline_at - now).total_seconds())

        context_token = current_task_lease.set(lease)
        heartbeat_job = asyncio.create_task(self._heartbeat_loop(asyncio.current_task()))
        try:
            answer_parts, done_payload, failure = [], None, None
            async with asyncio.timeout(timeout):
                async with self.session_factory() as runtime_session:
                    runtime = self.runtime_factory(
                        session=runtime_session, plugin_registry=self.tool_registry,
                        user_id=user_id, task_id=task_id,
                    )
                    async with aclosing(runtime.stream(ChatRequest(
                        conversation_id=conversation_id, message=prompt, task_type="background",
                    ))) as stream:
                        async for event in stream:
                            event_type = str(event.get("event") or "")
                            data = self._event_data(event)
                            if event_type == "token":
                                answer_parts.append(str(data.get("text") or ""))
                            elif event_type == "done":
                                done_payload = data
                            elif event_type == "error":
                                failure = str(data.get("message") or "Background agent task failed")
                            elif event_type == "tool_result" and data.get("error") == CONFIRMATION_REQUIRED_ERROR:
                                failure = ("This background task needs tool confirmation. "
                                           "Run the request in interactive chat to review and confirm the tool call.")
                            if event_type in PERSISTED_EVENT_TYPES:
                                await self._record_event(event_type, data)
            if failure or done_payload is None:
                failure = failure or "Agent task ended without a completion event"
                return await self._finish_or_retry(failure, retryable=self._is_retryable_error(failure))
            async with self.session_factory() as session:
                task = await lock_task_lease(session)
                task.status = TASK_STATUS_SUCCEEDED
                task.progress = 100
                task.trace_id = str(done_payload.get("trace_id") or task.trace_id or "") or None
                task.result = {
                    "answer": "".join(answer_parts), "conversation_id": str(conversation_id),
                    "citations": done_payload.get("citations") or [],
                    "tool_calls": done_payload.get("tool_calls") or [],
                    "model_route": done_payload.get("model_route"),
                }
                task.finished_at = datetime.now(UTC)
                task.lease_expires_at = None
                task.run_token = None
                await session.commit()
            return TaskRunOutcome()
        except TaskLeaseLost:
            return TaskRunOutcome()
        except asyncio.CancelledError:
            # A separate session ensures runtime rollback cannot undo cancellation.
            with suppress(TaskLeaseLost):
                await self._finish_or_retry(INTERRUPTED_TASK_ERROR, retryable=True)
            raise
        except Exception as exc:
            with suppress(TaskLeaseLost):
                return await self._finish_or_retry(
                    str(exc) or "Task attempt timed out", retryable=self._is_retryable_error(str(exc)),
                )
            return TaskRunOutcome()
        finally:
            heartbeat_job.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_job
            current_task_lease.reset(context_token)

    async def _record_event(self, event_type: str, data: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            task = await lock_task_lease(session)
            metadata = dict(task.metadata_ or {})
            events = list(metadata.get("events") or [])
            events.append({"type": event_type, "at": datetime.now(UTC).isoformat(),
                           "attempt": task.attempt_count, **self._compact_event_data(data)})
            metadata["events"] = events[-MAX_TASK_EVENTS:]
            task.metadata_ = metadata
            task.progress = max(task.progress or 0, min(self._progress(event_type, data), 95))
            if data.get("trace_id"):
                task.trace_id = str(data["trace_id"])
            await session.commit()

    async def _finish_failed(self, session: AsyncSession, task: Task, error: str) -> None:
        task.status = TASK_STATUS_FAILED
        task.error = error or "Background agent task failed"
        task.finished_at = datetime.now(UTC)
        task.next_attempt_at = None
        task.lease_expires_at = None
        task.run_token = None
        await session.commit()

    async def _finish_or_retry(self, error: str, *, retryable: bool) -> TaskRunOutcome:
        async with self.session_factory() as session:
            task = await lock_task_lease(session, live=False)
            now = datetime.now(UTC)
            delay = min(self.retry_base_seconds * 2 ** max(task.attempt_count - 1, 0), 3600)
            retry_at = now + timedelta(seconds=uniform(delay / 2, delay))
            if task.replay_blocked_at is not None:
                await self._finish_failed(session, task, f"{error} {REPLAY_BLOCKED_ERROR}")
            elif task.deadline_at is not None and retry_at >= task.deadline_at:
                await self._finish_failed(session, task, f"Task deadline exhausted: {error}")
            elif retryable and task.attempt_count < task.max_attempts:
                task.status = TASK_STATUS_QUEUED
                task.error = f"Attempt {task.attempt_count}/{task.max_attempts} failed; retry scheduled: {error}"
                task.finished_at = None
                task.next_attempt_at = retry_at
                task.lease_expires_at = None
                task.run_token = None
                metadata = dict(task.metadata_ or {})
                metadata["events"] = [*(metadata.get("events") or []), {
                    "type": "retry_scheduled", "at": now.isoformat(), "attempt": task.attempt_count,
                    "retry_at": retry_at.isoformat(), "error": error[:2000],
                }][-MAX_TASK_EVENTS:]
                task.metadata_ = metadata
                await session.commit()
                return TaskRunOutcome(retry_at=retry_at)
            else:
                await self._finish_failed(session, task, error)
            return TaskRunOutcome()

    async def _heartbeat_loop(self, owner: asyncio.Task) -> None:
        try:
            while True:
                await asyncio.sleep(min(self.heartbeat_seconds, self.lease_seconds / 3))
                async with self.session_factory() as session:
                    task = await lock_task_lease(session)
                    now = datetime.now(UTC)
                    task.heartbeat_at = now
                    task.lease_expires_at = min(now + timedelta(seconds=self.lease_seconds), task.deadline_at)
                    await session.commit()
        except TaskLeaseLost:
            owner.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task heartbeat failed; stopping worker until recovery")
            owner.cancel()

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

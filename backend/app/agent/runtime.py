from collections.abc import AsyncIterator
from dataclasses import asdict
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.conversation import Conversation, Message
from app.schemas import AgentExecutionState, AgentToolPlan, ChatRequest, ToolRunResponse
from app.services.model_gateway import ModelGateway
from app.services.plugin_registry import PluginRegistry
from app.services.rag import Citation, RagService
from app.services.tool_executor import ToolExecutor


class AgentRuntime:
    def __init__(
        self,
        session: AsyncSession,
        plugin_registry: PluginRegistry | None,
        user_id: UUID | None = None,
        model_gateway: ModelGateway | None = None,
        rag_service: RagService | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = 3,
    ) -> None:
        self.session = session
        self.plugin_registry = plugin_registry
        self.user_id = user_id
        self.model_gateway = model_gateway or ModelGateway()
        self.rag = rag_service or RagService(session)
        self.tool_executor = tool_executor or ToolExecutor()
        self.max_tool_rounds = max(max_tool_rounds, 0)
        self.settings = get_settings()

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, str]]:
        state = AgentExecutionState(user_id=self.user_id, message=request.message)
        try:
            async for event in self._execute(request, state):
                yield event
        except Exception as exc:
            yield self._event("error", {"message": str(exc), "trace_id": state.trace_id})

    async def _execute(
        self, request: ChatRequest, state: AgentExecutionState
    ) -> AsyncIterator[dict[str, str]]:
        yield self._event("status", {"label": "ensure_conversation", "trace_id": state.trace_id})
        conversation = await self._ensure_conversation(request)
        state.conversation_id = conversation.id

        yield self._event("status", {"label": "load_history", "trace_id": state.trace_id})
        state.history = await self._load_recent_history(conversation.id)
        await self._auto_title_conversation(conversation, request.message, state.history)

        yield self._event("status", {"label": "save_user_message", "trace_id": state.trace_id})
        await self._save_message(conversation.id, "user", request.message)

        yield self._event("status", {"label": "retrieving_context"})
        state.citations = self._dump_citations(
            await self._retrieve_context(request.message, conversation.id)
        )

        route = self.model_gateway.route(request.task_type, request.message)
        async for event in self._run_tool_loop(state):
            yield event

        yield self._event("status", {"label": "generating", "model": route.model_name})
        async for token in self._generate_answer(state, route.model_name):
            yield self._event("token", {"text": token})

        yield self._event("status", {"label": "save_assistant_message", "trace_id": state.trace_id})
        await self._save_assistant_message(state, route)
        yield self._event(
            "done",
            {
                "conversation_id": str(conversation.id),
                "citations": state.citations,
                "trace_id": state.trace_id,
            },
        )

    async def _retrieve_context(
        self, message: str, conversation_id: UUID | None
    ) -> list[Citation]:
        return await self.rag.search(message, conversation_id)

    async def _generate_answer(
        self, state: AgentExecutionState, model_name: str
    ) -> AsyncIterator[str]:
        response_parts: list[str] = []
        async for token in self.model_gateway.stream_reply(
            model_name=model_name,
            prompt=state.message,
            context=self._answer_context(state),
            history=state.history,
        ):
            response_parts.append(token)
            yield token
        state.final_answer = "".join(response_parts)

    def _answer_context(self, state: AgentExecutionState) -> list[str]:
        context = [str(item["content"]) for item in state.citations if item.get("content")]
        context.extend(f"Tool observation:\n{observation}" for observation in state.observations)
        return context

    async def _save_assistant_message(self, state: AgentExecutionState, route) -> None:
        if state.conversation_id is None:
            raise RuntimeError("Cannot save assistant message without a conversation")
        await self._save_message(
            state.conversation_id,
            "assistant",
            state.final_answer,
            metadata={
                "citations": state.citations,
                "trace_id": state.trace_id,
                "model_route": asdict(route),
            },
            model_name=route.model_name,
        )

    def _dump_citations(self, citations: list[Citation]) -> list[dict]:
        return [item.model_dump() for item in citations]

    def _plan_next_step(self, state: AgentExecutionState) -> AgentToolPlan:
        if any(item.get("tool_name") == "read_file" for item in state.tool_calls):
            return AgentToolPlan(no_tool=True, reason="The requested file has already been read.")
        path = self._extract_read_file_path(state.message)
        if path is not None and self._requests_file_read(state.message):
            tool = self.plugin_registry.get("read_file") if self.plugin_registry else None
            return AgentToolPlan(
                no_tool=False,
                tool_name="read_file",
                arguments={"path": path},
                reason="The user asked to read a local file before answering.",
                requires_confirmation=bool(tool and tool.manifest.requires_confirmation),
            )
        return AgentToolPlan(no_tool=True, reason="No tool is needed for this message.")

    async def _run_tool_loop(
        self, state: AgentExecutionState
    ) -> AsyncIterator[dict[str, str]]:
        rounds = 0
        while rounds < self.max_tool_rounds:
            yield self._event("status", {"label": "planning", "trace_id": state.trace_id})
            state.plan = self._plan_next_step(state)
            yield self._event("plan", state.plan.model_dump())
            if state.plan.no_tool:
                return
            async for event in self._maybe_execute_tool(state):
                yield event
            rounds += 1

        state.plan = AgentToolPlan(
            no_tool=True,
            reason=f"Stopped after the maximum of {self.max_tool_rounds} tool round(s).",
        )
        yield self._event("plan", state.plan.model_dump())

    async def _maybe_execute_tool(
        self, state: AgentExecutionState
    ) -> AsyncIterator[dict[str, str]]:
        plan = state.plan
        if plan.no_tool or plan.tool_name is None:
            return

        yield self._event(
            "tool_call",
            {
                "tool_name": plan.tool_name,
                "arguments": plan.arguments,
                "reason": plan.reason,
                "trace_id": state.trace_id,
            },
        )
        result = await self._execute_tool_plan(state)
        state.tool_calls.append(
            {
                "tool_name": plan.tool_name,
                "arguments": plan.arguments,
                "reason": plan.reason,
                "requires_confirmation": plan.requires_confirmation,
                "result": result.model_dump(),
            }
        )
        state.observations.append(self._format_tool_observation(result))
        yield self._event("tool_result", result.model_dump())

    async def _execute_tool_plan(self, state: AgentExecutionState) -> ToolRunResponse:
        plan = state.plan
        if plan.tool_name is None:
            return ToolRunResponse(
                tool_name="unknown",
                status="failed",
                error="Tool plan did not include a tool name",
            )
        tool = self.plugin_registry.get(plan.tool_name) if self.plugin_registry else None
        if tool is None:
            return ToolRunResponse(
                tool_name=plan.tool_name,
                status="failed",
                error=f"Tool is not available: {plan.tool_name}",
            )
        return await self.tool_executor.run(
            tool,
            plan.arguments,
            confirmed=not plan.requires_confirmation,
            session=self.session,
            user_id=state.user_id,
            conversation_id=state.conversation_id,
        )

    def _format_tool_observation(self, result: ToolRunResponse) -> str:
        if result.status == "success":
            output = result.output_summary
            if output is None:
                output = json.dumps(result.output, ensure_ascii=False, default=str)
            return f"{result.tool_name} succeeded: {output}"
        return f"{result.tool_name} failed with status {result.status}: {result.error}"

    def _requests_file_read(self, message: str) -> bool:
        lowered = message.lower()
        read_terms = ("read", "open", "inspect", "读取", "读一下", "查看", "看看")
        summary_terms = ("summarize", "summary", "总结", "概括", "归纳")
        return any(term in lowered for term in read_terms) and (
            any(term in lowered for term in summary_terms) or self._extract_read_file_path(message) is not None
        )

    def _extract_read_file_path(self, message: str) -> str | None:
        quoted = re.search(r"[`\"'“”‘’]([^`\"'“”‘’]+\.[A-Za-z0-9]+)[`\"'“”‘’]", message)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(r"(?P<path>[\w./\\-]+\.(?:md|txt|py|json|ya?ml|toml))", message, re.IGNORECASE)
        if bare:
            return bare.group("path").strip(" ，,。.!！?；;：:")
        return None

    async def _ensure_conversation(self, request: ChatRequest) -> Conversation:
        if request.conversation_id is not None:
            statement = select(Conversation).where(Conversation.id == request.conversation_id)
            if self.user_id is not None:
                statement = statement.where(Conversation.user_id == self.user_id)
            result = await self.session.execute(statement)
            conversation = result.scalar_one_or_none()
            if conversation is not None:
                return conversation

        conversation = Conversation(title="New conversation", user_id=self.user_id)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def _load_recent_history(self, conversation_id: UUID) -> list[dict[str, str]]:
        limit = max(self.settings.agent_memory_message_limit, 0)
        if limit == 0:
            return []

        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return [{"role": message.role, "content": message.content} for message in messages]

    async def _auto_title_conversation(
        self,
        conversation: Conversation,
        message: str,
        history: list[dict[str, str]],
    ) -> None:
        if history or conversation.title.strip() != "New conversation":
            return
        conversation.title = self._summarize_title(message)
        await self.session.commit()

    def _summarize_title(self, message: str) -> str:
        clean = " ".join(message.strip().split())
        if not clean:
            return "New conversation"
        prefixes = (
            "请记住",
            "请",
            "帮我",
            "帮忙",
            "能否",
            "可以",
            "please",
            "can you",
            "could you",
        )
        lowered = clean.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                clean = clean[len(prefix) :].strip(" ：:，,。.!！?")
                break
        return clean[:40].rstrip(" ：:，,。.!！?") or "New conversation"

    async def _save_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
        model_name: str | None = None,
    ) -> None:
        self.session.add(
            Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                model_name=model_name,
                metadata_=metadata or {},
            )
        )
        await self.session.commit()

    def _event(self, event_type: str, data: dict) -> dict[str, str]:
        return {"event": event_type, "data": json.dumps(data)}

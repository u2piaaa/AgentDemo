from collections.abc import AsyncIterator
from dataclasses import asdict
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_agent_graph
from app.agent.state import AgentGraphState
from app.core.config import get_settings
from app.core.mcp_security import McpIdentity
from app.models.conversation import Conversation, MemorySummary, Message
from app.schemas import (
    AgentExecutionState,
    AgentToolPlan,
    ChatRequest,
    ToolConfirmationRequest,
    ToolRunResponse,
)
from app.services.model_gateway import ModelGateway
from app.services.plugin_registry import TOOL_PROVIDER_MCP_SERVER, PluginRegistry
from app.services.rag import Citation, RagService
from app.services.tool_executor import ToolExecutor

WEB_SEARCH_TOOL_NAME = "web_search"
MCP_FETCH_TOOL_NAME = "mcp.fetch.fetch"
FETCH_DEFAULT_MAX_LENGTH = 8000
FETCH_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
FETCH_INTENT_TERMS = (
    "fetch",
    "read",
    "retrieve",
    "summarize",
    "summarise",
    "summary",
    "analyze",
    "analyse",
    "page",
    "webpage",
    "\u603b\u7ed3",
    "\u6458\u8981",
    "\u6982\u62ec",
    "\u6293\u53d6",
    "\u8bfb\u53d6",
    "\u5206\u6790",
    "\u7f51\u9875",
    "\u9875\u9762",
)
WEB_SEARCH_TRIGGER_TERMS = (
    "web search",
    "search web",
    "search online",
    "online search",
    "internet search",
    "\u8054\u7f51\u641c\u7d22",
    "\u641c\u7d22\u4e00\u4e0b",
    "\u4e0a\u7f51\u67e5",
    "\u7f51\u4e0a\u641c",
    "\u67e5\u6700\u65b0",
)
WEB_SEARCH_CURRENT_TERMS = (
    "latest",
    "current",
    "today",
    "recent",
    "recently",
    "news",
    "\u6700\u65b0",
    "\u6700\u8fd1",
    "\u8fd1\u671f",
    "\u4eca\u5929",
    "\u73b0\u5728",
    "\u5f53\u524d",
    "\u65b0\u95fb",
    "\u6d88\u606f",
    "\u8d44\u8baf",
)
WEB_SEARCH_FACT_QUERY_TERMS = (
    "price",
    "weather",
    "score",
    "stock",
    "release",
    "version",
    "status",
    "model",
    "news",
    "\u4ec0\u4e48",
    "\u662f\u8c01",
    "\u591a\u5c11",
    "\u4ef7\u683c",
    "\u5929\u6c14",
    "\u80a1\u4ef7",
    "\u6c47\u7387",
    "\u6bd4\u5206",
    "\u884c\u60c5",
    "\u7248\u672c",
    "\u53d1\u5e03",
    "\u65b0\u95fb",
    "\u6d88\u606f",
    "\u8d44\u8baf",
)
WEB_SEARCH_QUESTION_RE = re.compile(r"\b(who|what|when|where|which|how many|how much)\b")


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
        self.mcp_client = getattr(plugin_registry, "mcp_client", None)
        self.user_id = user_id
        self.model_gateway = model_gateway or ModelGateway()
        self.rag = rag_service or RagService(session, user_id=user_id)
        self.tool_executor = tool_executor or ToolExecutor()
        self.max_tool_rounds = max(max_tool_rounds, 0)
        self.settings = get_settings()

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, str]]:
        trace_id = uuid4().hex
        state: AgentGraphState = {
            "user_id": self.user_id,
            "conversation_id": request.conversation_id,
            "message": request.message,
            "task_type": request.task_type,
            "history": [],
            "memory_summaries": [],
            "citations": [],
            "mcp_resources": [],
            "mcp_prompts": [],
            "plan": AgentToolPlan(),
            "tool_calls": [],
            "observations": [],
            "final_answer": "",
            "trace_id": trace_id,
            "tool_rounds": 0,
            "max_tool_rounds": self.max_tool_rounds,
            "save_user_message": True,
            "events": [],
        }
        try:
            graph = build_agent_graph(self, mode="chat")
            config = self._graph_config(state)
            async for event in graph.astream(state, config=config, stream_mode="custom"):
                yield event
        except Exception as exc:
            yield self._event("error", {"message": str(exc), "trace_id": trace_id})

    async def stream_confirmed_tool(
        self, request: ToolConfirmationRequest
    ) -> AsyncIterator[dict[str, str]]:
        trace_id = uuid4().hex
        state: AgentGraphState = {
            "user_id": self.user_id,
            "conversation_id": request.conversation_id,
            "message": request.message,
            "task_type": request.task_type,
            "history": [],
            "memory_summaries": [],
            "citations": [],
            "mcp_resources": [],
            "mcp_prompts": [],
            "plan": AgentToolPlan(),
            "tool_calls": [],
            "observations": [],
            "final_answer": "",
            "trace_id": trace_id,
            "tool_rounds": 0,
            "max_tool_rounds": self.max_tool_rounds,
            "save_user_message": False,
            "confirmed_tool_name": request.tool_name,
            "confirmed_arguments": request.arguments,
            "confirmed_reason": request.reason,
            "events": [],
        }
        try:
            graph = build_agent_graph(self, mode="confirmed_tool")
            config = self._graph_config(state)
            async for event in graph.astream(state, config=config, stream_mode="custom"):
                yield event
        except Exception as exc:
            yield self._event("error", {"message": str(exc), "trace_id": trace_id})

    def _graph_config(self, state: AgentGraphState) -> dict:
        conversation_id = state.get("conversation_id")
        if conversation_id is None:
            return {}
        return {"configurable": {"thread_id": str(conversation_id)}}

    async def _ensure_conversation_from_state(self, state: AgentGraphState) -> Conversation:
        return await self._ensure_conversation(
            ChatRequest(
                conversation_id=state.get("conversation_id"),
                message=state["message"],
                task_type=state.get("task_type", "conversation"),
            )
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
        context = [f"Memory summary:\n{summary}" for summary in state.memory_summaries]
        tool_context = self._available_tools_context()
        if tool_context:
            context.append(tool_context)
        context.extend(str(item["content"]) for item in state.citations if item.get("content"))
        context.extend(
            f"MCP resource {item.get('uri')} from {item.get('server_name')}:\n{item.get('text')}"
            for item in state.mcp_resources
            if item.get("text")
        )
        context.extend(
            f"MCP prompt {item.get('name')} from {item.get('server_name')}:\n{item.get('content')}"
            for item in state.mcp_prompts
            if item.get("content")
        )
        context.extend(f"Tool observation:\n{observation}" for observation in state.observations)
        return context

    def _available_tools_context(self) -> str:
        tools = self._available_tools()
        if not tools:
            return ""
        lines = [
            "Available runtime tools. If the user asks whether a tool exists, answer from this list:"
        ]
        lines.extend(
            f"- {tool.manifest.name}: {tool.manifest.description}"
            for tool in tools
        )
        return "\n".join(lines)

    def _available_tools(self) -> list:
        if not self.plugin_registry or not hasattr(self.plugin_registry, "list_tools"):
            return []
        return [
            tool
            for tool in self.plugin_registry.list_tools()
            if tool.manifest.enabled
        ]

    def _tool_availability_answer(self, message: str) -> str | None:
        lowered = message.lower()
        tools = self._available_tools()
        if not tools:
            if self._requests_tool_inventory(lowered):
                return "\u5f53\u524d\u6ca1\u6709\u5df2\u52a0\u8f7d\u7684\u8fd0\u884c\u65f6\u5de5\u5177\u3002"
            return None

        matching_tools = [tool for tool in tools if tool.manifest.name.lower() in lowered]
        if matching_tools:
            if not self._requests_tool_inventory(lowered):
                return None
            tool = matching_tools[0]
            answer = (
                f"\u6709\uff0c\u6211\u5df2\u7ecf\u52a0\u8f7d\u4e86 `{tool.manifest.name}` "
                f"\u5de5\u5177\u3002\u7528\u9014\uff1a{tool.manifest.description}"
            )
            if tool.manifest.name == WEB_SEARCH_TOOL_NAME:
                answer += (
                    "\u3002\u5b83\u53ef\u4ee5\u88ab\u81ea\u52a8\u89e6\u53d1\u6216\u76f4\u63a5\u8c03\u7528\uff0c"
                    "\u4f46\u771f\u5b9e\u8054\u7f51\u641c\u7d22\u8fd8\u9700\u8981\u5728\u540e\u7aef `.env` "
                    "\u91cc\u914d\u7f6e `WEB_SEARCH_PROVIDER` \u548c\u5bf9\u5e94 API key\u3002"
                )
            return answer

        if self._requests_tool_inventory(lowered):
            names = ", ".join(f"`{tool.manifest.name}`" for tool in tools)
            return f"\u5f53\u524d\u5df2\u52a0\u8f7d\u7684\u5de5\u5177\u6709\uff1a{names}\u3002"
        return None

    def _requests_tool_inventory(self, lowered_message: str) -> bool:
        return (
            bool(re.search(r"\b(available|exists|have|check|list)\b", lowered_message))
            or bool(re.search(r"\btools?\b", lowered_message))
            or any(
                term in lowered_message
                for term in ("\u5de5\u5177", "\u6709\u6ca1\u6709", "\u6709\u65e0", "\u662f\u5426", "\u68c0\u67e5", "\u5217\u51fa", "\u54ea\u4e9b")
            )
        )

    async def _save_assistant_message(self, state: AgentExecutionState, route) -> None:
        if state.conversation_id is None:
            raise RuntimeError("Cannot save assistant message without a conversation")
        await self._save_message(
            state.conversation_id,
            "assistant",
            state.final_answer,
            metadata={
                "citations": state.citations,
                "mcp_resources": state.mcp_resources,
                "mcp_prompts": state.mcp_prompts,
                "tool_calls": state.tool_calls,
                "memory_summaries": state.memory_summaries,
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
        if any(item.get("tool_name") == MCP_FETCH_TOOL_NAME for item in state.tool_calls):
            return AgentToolPlan(no_tool=True, reason="The requested web page has already been fetched.")
        if any(item.get("tool_name") == WEB_SEARCH_TOOL_NAME for item in state.tool_calls):
            return AgentToolPlan(no_tool=True, reason="The web has already been searched.")
        path = self._extract_read_file_path(state.message)
        if path is not None and self._requests_file_read(state.message):
            tool = self.plugin_registry.get("read_file") if self.plugin_registry else None
            return AgentToolPlan(
                no_tool=False,
                tool_name="read_file",
                provider=tool.provider if tool else "local_plugin",
                provider_tool_id=tool.provider_tool_id if tool else "read_file",
                server_name=tool.server_name if tool else None,
                arguments={"path": path},
                reason="The user asked to read a local file before answering.",
                requires_confirmation=bool(tool and tool.manifest.requires_confirmation),
            )
        fetch_plan = self._plan_fetch_tool(state)
        if fetch_plan is not None:
            return fetch_plan
        if self._requests_web_search(state.message):
            tool = self.plugin_registry.get(WEB_SEARCH_TOOL_NAME) if self.plugin_registry else None
            return AgentToolPlan(
                no_tool=False,
                tool_name=WEB_SEARCH_TOOL_NAME,
                provider=tool.provider if tool else "local_plugin",
                provider_tool_id=tool.provider_tool_id if tool else WEB_SEARCH_TOOL_NAME,
                server_name=tool.server_name if tool else None,
                arguments=self._web_search_arguments(state.message),
                reason="The user asked for current or external web information.",
                requires_confirmation=bool(tool and tool.manifest.requires_confirmation),
            )
        mcp_plan = self._plan_mcp_tool(state)
        if mcp_plan is not None:
            return mcp_plan
        return AgentToolPlan(no_tool=True, reason="No tool is needed for this message.")

    def _plan_fetch_tool(self, state: AgentExecutionState) -> AgentToolPlan | None:
        url = self._extract_fetch_url(state.message)
        if url is None or not self._requests_fetch_url(state.message):
            return None
        tool = self.plugin_registry.get(MCP_FETCH_TOOL_NAME) if self.plugin_registry else None
        if tool is None:
            return AgentToolPlan(
                no_tool=True,
                reason="The user asked to read a specific web page, but the fetch MCP tool is not available.",
            )
        return AgentToolPlan(
            no_tool=False,
            tool_name=tool.manifest.name,
            provider=tool.provider,
            provider_tool_id=tool.provider_tool_id,
            server_name=tool.server_name,
            arguments={
                "url": url,
                "max_length": max(
                    1,
                    min(FETCH_DEFAULT_MAX_LENGTH, self.settings.max_tool_output_chars),
                ),
            },
            reason="The user asked to fetch and summarize a specific web page URL.",
            requires_confirmation=tool.manifest.requires_confirmation,
        )

    def _plan_mcp_tool(self, state: AgentExecutionState) -> AgentToolPlan | None:
        if not self.plugin_registry or not hasattr(self.plugin_registry, "list_tools"):
            return None
        candidates = [
            tool
            for tool in self.plugin_registry.list_tools()
            if tool.provider == TOOL_PROVIDER_MCP_SERVER
        ]
        if not candidates:
            return None
        planned = self.model_gateway.plan_tool_call(
            state.message,
            [tool.to_read_model().model_dump() for tool in candidates],
        )
        if planned.no_tool or planned.tool_name is None:
            return None
        tool = self.plugin_registry.get(planned.tool_name)
        if tool is None:
            return None
        return AgentToolPlan(
            no_tool=False,
            tool_name=tool.manifest.name,
            provider=tool.provider,
            provider_tool_id=tool.provider_tool_id,
            server_name=tool.server_name,
            arguments=planned.arguments or {},
            reason=planned.reason,
            requires_confirmation=tool.manifest.requires_confirmation,
        )

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
                "provider": plan.provider,
                "provider_tool_id": plan.provider_tool_id,
                "server_name": plan.server_name,
                "arguments": plan.arguments,
                "reason": plan.reason,
                "requires_confirmation": plan.requires_confirmation,
                "trace_id": state.trace_id,
            },
        )
        result = await self._execute_tool_plan(state)
        state.tool_calls.append(
            {
                "tool_name": plan.tool_name,
                "provider": plan.provider,
                "provider_tool_id": plan.provider_tool_id,
                "server_name": plan.server_name,
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
            identity=McpIdentity(user_id=state.user_id),
        )

    async def _load_mcp_resources_for_context(self, message: str) -> list[dict[str, str]]:
        if self.mcp_client is None:
            return []
        lowered = message.lower()
        selected = []
        for resource in await self.mcp_client.list_resources():
            uri = str(resource.get("uri") or "")
            name = str(resource.get("name") or "")
            if uri.lower() not in lowered and name.lower() not in lowered:
                continue
            loaded = await self.mcp_client.read_resource(str(resource["server_name"]), uri)
            selected.append(
                {
                    "server_name": str(resource["server_name"]),
                    "uri": uri,
                    "name": name,
                    "text": str(loaded.get("text") or loaded.get("content") or ""),
                }
            )
        return selected

    async def _load_mcp_prompts_for_context(self, message: str) -> list[dict[str, str]]:
        if self.mcp_client is None:
            return []
        lowered = message.lower()
        selected = []
        for prompt in await self.mcp_client.list_prompts():
            name = str(prompt.get("name") or "")
            if name.lower() not in lowered:
                continue
            loaded = await self.mcp_client.get_prompt(str(prompt["server_name"]), name)
            messages = loaded.get("messages") or []
            content = "\n".join(
                str(item.get("content"))
                for item in messages
                if isinstance(item, dict) and item.get("content")
            )
            selected.append(
                {
                    "server_name": str(prompt["server_name"]),
                    "name": name,
                    "content": content,
                }
            )
        return selected

    def _format_tool_observation(self, result: ToolRunResponse) -> str:
        if result.status == "success":
            output = result.output_summary
            if output is None:
                output = json.dumps(result.output, ensure_ascii=False, default=str)
            if result.tool_name == WEB_SEARCH_TOOL_NAME:
                return (
                    "Web search results. Use these results for current external facts and cite "
                    f"result URLs when answering:\n{output}"
                )
            if result.tool_name == MCP_FETCH_TOOL_NAME:
                url = ""
                if isinstance(result.output, dict):
                    raw = result.output.get("raw")
                    if isinstance(raw, dict):
                        url = str(raw.get("url") or "")
                return (
                    "Fetched web page content. Summarize the content from this page and preserve "
                    f"the source URL in the answer: {url}\n{output}"
                )
            return f"{result.tool_name} succeeded: {output}"
        return f"{result.tool_name} failed with status {result.status}: {result.error}"

    def _requests_fetch_url(self, message: str) -> bool:
        lowered = message.lower()
        return any(term in lowered for term in FETCH_INTENT_TERMS)

    def _extract_fetch_url(self, message: str) -> str | None:
        match = FETCH_URL_RE.search(message)
        if match is None:
            return None
        return match.group(0).rstrip(".,;:!?)\u3002\uff0c\uff1b\uff1a\uff01\uff1f\uff09\u3011\u300d'\"")

    def _requests_web_search(self, message: str) -> bool:
        lowered = message.lower()
        if any(term in lowered for term in WEB_SEARCH_TRIGGER_TERMS):
            return True
        if not any(term in lowered for term in WEB_SEARCH_CURRENT_TERMS):
            return False
        return (
            WEB_SEARCH_QUESTION_RE.search(lowered) is not None
            or "\uff1f" in lowered
            or "?" in lowered
            or any(term in lowered for term in WEB_SEARCH_FACT_QUERY_TERMS)
        )

    def _web_search_arguments(self, message: str) -> dict[str, str | int]:
        arguments: dict[str, str | int] = {"query": " ".join(message.strip().split())}
        recency_days = self._infer_web_search_recency_days(message)
        if recency_days is not None:
            arguments["recency_days"] = recency_days
        return arguments

    def _infer_web_search_recency_days(self, message: str) -> int | None:
        lowered = message.lower()
        if any(term in lowered for term in ("today", "now", "\u4eca\u5929", "\u73b0\u5728")):
            return 1
        if any(term in lowered for term in ("latest", "current", "news", "\u6700\u65b0", "\u65b0\u95fb")):
            return 7
        return None

    def _requests_file_read(self, message: str) -> bool:
        lowered = message.lower()
        read_terms = ("read", "open", "inspect", "读取", "读一下", "查看", "看看")
        summary_terms = ("summarize", "summary", "总结", "概括", "归纳")
        return any(term in lowered for term in read_terms) and (
            any(term in lowered for term in summary_terms)
            or self._extract_read_file_path(message) is not None
        )

    def _extract_read_file_path(self, message: str) -> str | None:
        quoted = re.search(r"[`\"'“”‘’]([^`\"'“”‘’]+\.[A-Za-z0-9]+)[`\"'“”‘’]", message)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(
            r"(?P<path>[\w./\\-]+\.(?:md|txt|py|json|ya?ml|toml))",
            message,
            re.IGNORECASE,
        )
        if bare:
            return bare.group("path").strip(" 。.!！？,，")
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
        if conversation.id is None:
            conversation.id = uuid4()
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
        messages = [item for item in result.scalars().all() if isinstance(item, Message)]
        messages.reverse()
        return [{"role": message.role, "content": message.content} for message in messages]

    async def _load_active_memory_summaries(self, conversation_id: UUID) -> list[str]:
        result = await self.session.execute(
            select(MemorySummary)
            .where(MemorySummary.conversation_id == conversation_id)
            .where(MemorySummary.valid_to.is_(None))
            .order_by(MemorySummary.updated_at.desc(), MemorySummary.created_at.desc())
            .limit(3)
        )
        return [
            item.summary
            for item in result.scalars().all()
            if isinstance(item, MemorySummary) and item.summary
        ]

    async def _load_active_memory_records(self, conversation_id: UUID) -> list[MemorySummary]:
        result = await self.session.execute(
            select(MemorySummary)
            .where(MemorySummary.conversation_id == conversation_id)
            .where(MemorySummary.valid_to.is_(None))
            .order_by(MemorySummary.updated_at.desc(), MemorySummary.created_at.desc())
            .limit(1)
        )
        return [
            item
            for item in result.scalars().all()
            if isinstance(item, MemorySummary) and item.summary
        ]

    async def _load_summary_source_messages(self, conversation_id: UUID) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.asc())
        )
        return [item for item in result.scalars().all() if isinstance(item, Message)]

    async def _maybe_update_memory_summary(self, conversation_id: UUID) -> None:
        messages = await self._load_summary_source_messages(conversation_id)
        if len(messages) <= max(self.settings.agent_memory_message_limit, 0):
            return

        existing_records = await self._load_active_memory_records(conversation_id)
        existing_summary = existing_records[0].summary if existing_records else None
        try:
            summary = await self.model_gateway.summarize_messages(
                [{"role": item.role, "content": item.content} for item in messages],
                existing_summary=existing_summary,
            )
        except Exception:
            return
        if not summary:
            return

        if existing_records:
            existing_records[0].summary = summary
            await self.session.commit()
            return

        self.session.add(
            MemorySummary(
                conversation_id=conversation_id,
                summary=summary,
                valid_from=messages[0].created_at if messages else None,
                valid_to=None,
            )
        )
        await self.session.commit()

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
            "请帮我",
            "请帮忙",
            "能否请",
            "可以帮我",
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
        for prefix in sorted(prefixes, key=len, reverse=True):
            if lowered.startswith(prefix):
                clean = clean[len(prefix) :].strip(" ：:，,。.!！？")
                break
        return clean[:40].rstrip(" ：:，,。.!！？") or "New conversation"

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
        return {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}

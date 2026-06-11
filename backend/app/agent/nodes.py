from typing import TYPE_CHECKING, Any

from app.agent.events import done_event, emit_event, emit_runtime_event, emit_status
from app.agent.state import AgentGraphState
from app.schemas import AgentExecutionState, AgentToolPlan

if TYPE_CHECKING:
    from app.agent.runtime import AgentRuntime


class AgentGraphNodes:
    def __init__(self, runtime: "AgentRuntime") -> None:
        self.runtime = runtime

    async def ensure_conversation(self, state: AgentGraphState) -> dict[str, Any]:
        emit_status("ensure_conversation", state)
        conversation = await self.runtime._ensure_conversation_from_state(state)
        return {"conversation_id": conversation.id, "conversation": conversation}

    async def load_context(self, state: AgentGraphState) -> dict[str, Any]:
        emit_status("load_history", state)
        conversation_id = state.get("conversation_id")
        if conversation_id is None:
            raise RuntimeError("Cannot load history without a conversation")
        history = await self.runtime._load_recent_history(conversation_id)
        memory_summaries = await self.runtime._load_active_memory_summaries(conversation_id)
        conversation = state.get("conversation")
        if conversation is not None and state.get("save_user_message", True):
            await self.runtime._auto_title_conversation(conversation, state["message"], history)
        return {"history": history, "memory_summaries": memory_summaries}

    async def save_user_message(self, state: AgentGraphState) -> dict[str, Any]:
        if not state.get("save_user_message", True):
            return {}
        emit_status("save_user_message", state)
        conversation_id = state.get("conversation_id")
        if conversation_id is None:
            raise RuntimeError("Cannot save user message without a conversation")
        await self.runtime._save_message(conversation_id, "user", state["message"])
        return {}

    async def retrieve_context(self, state: AgentGraphState) -> dict[str, Any]:
        emit_status("retrieving_context", state)
        conversation_id = state.get("conversation_id")
        citations = self.runtime._dump_citations(
            await self.runtime._retrieve_context(state["message"], conversation_id)
        )
        mcp_resources = await self.runtime._load_mcp_resources_for_context(state["message"])
        mcp_prompts = await self.runtime._load_mcp_prompts_for_context(state["message"])
        return {
            "citations": citations,
            "mcp_resources": mcp_resources,
            "mcp_prompts": mcp_prompts,
            "route": self.runtime.model_gateway.route(
                state.get("task_type", "conversation"), state["message"]
            ),
        }

    async def plan(self, state: AgentGraphState) -> dict[str, Any]:
        confirmed_tool_name = state.get("confirmed_tool_name")
        if confirmed_tool_name:
            tool = (
                self.runtime.plugin_registry.get(confirmed_tool_name)
                if self.runtime.plugin_registry
                else None
            )
            plan = AgentToolPlan(
                no_tool=False,
                tool_name=confirmed_tool_name,
                provider=tool.provider if tool else "local_plugin",
                provider_tool_id=tool.provider_tool_id if tool else confirmed_tool_name,
                server_name=tool.server_name if tool else None,
                arguments=state.get("confirmed_arguments", {}),
                reason=state.get("confirmed_reason", "Confirmed by the user."),
                requires_confirmation=False,
            )
            emit_event("plan", plan.model_dump())
            return {"plan": plan}

        if state.get("tool_rounds", 0) >= state.get("max_tool_rounds", 0):
            plan = AgentToolPlan(
                no_tool=True,
                reason=(
                    f"Stopped after the maximum of {state.get('max_tool_rounds', 0)} "
                    "tool round(s)."
                ),
            )
            emit_event("plan", plan.model_dump())
            return {"plan": plan}

        emit_status("planning", state)
        execution_state = graph_state_to_execution_state(state)
        plan = self.runtime._plan_next_step(execution_state)
        emit_event("plan", plan.model_dump())
        return {"plan": plan}

    async def execute_tool(self, state: AgentGraphState) -> dict[str, Any]:
        execution_state = graph_state_to_execution_state(state)
        async for event in self.runtime._maybe_execute_tool(execution_state):
            emit_runtime_event(event)
        return {
            **execution_state_updates(execution_state),
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }

    async def generate_answer(self, state: AgentGraphState) -> dict[str, Any]:
        route = state["route"]
        execution_state = graph_state_to_execution_state(state)
        tool_availability_answer = None
        if not state.get("confirmed_tool_name"):
            tool_availability_answer = self.runtime._tool_availability_answer(
                execution_state.message
            )
        tool_failure_answer = self.runtime._tool_failure_answer(execution_state)

        if execution_state.final_answer:
            emit_event("status", {"label": "generating", "model": "runtime"})
            emit_event("token", {"text": execution_state.final_answer})
        elif tool_availability_answer is not None:
            emit_event("status", {"label": "generating", "model": "runtime"})
            execution_state.final_answer = tool_availability_answer
            emit_event("token", {"text": tool_availability_answer})
        elif tool_failure_answer is not None:
            emit_event("status", {"label": "generating", "model": "runtime"})
            execution_state.final_answer = tool_failure_answer
            emit_event("token", {"text": tool_failure_answer})
        else:
            emit_event("status", {"label": "generating", "model": route.model_name})
            async for token in self.runtime._generate_answer(execution_state, route.model_name):
                emit_event("token", {"text": token})

        return execution_state_updates(execution_state)

    async def save_assistant_message(self, state: AgentGraphState) -> dict[str, Any]:
        emit_status("save_assistant_message", state)
        execution_state = graph_state_to_execution_state(state)
        await self.runtime._save_assistant_message(execution_state, state["route"])
        return execution_state_updates(execution_state)

    async def update_memory_summary(self, state: AgentGraphState) -> dict[str, Any]:
        conversation_id = state.get("conversation_id")
        if conversation_id is None:
            raise RuntimeError("Cannot update memory summary without a conversation")
        await self.runtime._maybe_update_memory_summary(conversation_id)
        execution_state = graph_state_to_execution_state(state)
        emit_runtime_event(done_event(conversation_id, execution_state, state["route"]))
        return {}


def graph_state_to_execution_state(state: AgentGraphState) -> AgentExecutionState:
    return AgentExecutionState(
        user_id=state.get("user_id"),
        conversation_id=state.get("conversation_id"),
        message=state["message"],
        history=list(state.get("history", [])),
        memory_summaries=list(state.get("memory_summaries", [])),
        citations=list(state.get("citations", [])),
        mcp_resources=list(state.get("mcp_resources", [])),
        mcp_prompts=list(state.get("mcp_prompts", [])),
        plan=state.get("plan", AgentToolPlan()),
        tool_calls=list(state.get("tool_calls", [])),
        observations=list(state.get("observations", [])),
        final_answer=state.get("final_answer", ""),
        trace_id=state["trace_id"],
    )


def execution_state_updates(state: AgentExecutionState) -> dict[str, Any]:
    return {
        "conversation_id": state.conversation_id,
        "history": state.history,
        "memory_summaries": state.memory_summaries,
        "citations": state.citations,
        "mcp_resources": state.mcp_resources,
        "mcp_prompts": state.mcp_prompts,
        "plan": state.plan,
        "tool_calls": state.tool_calls,
        "observations": state.observations,
        "final_answer": state.final_answer,
        "trace_id": state.trace_id,
    }

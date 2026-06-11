from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import AgentGraphNodes
from app.agent.state import AgentGraphState


GraphMode = Literal["chat", "confirmed_tool"]


def build_agent_graph(runtime, mode: GraphMode = "chat"):
    nodes = AgentGraphNodes(runtime)
    graph = StateGraph(AgentGraphState)

    graph.add_node("ensure_conversation", nodes.ensure_conversation)
    graph.add_node("load_context", nodes.load_context)
    graph.add_node("save_user_message", nodes.save_user_message)
    graph.add_node("preflight_confirmable_tool", nodes.preflight_confirmable_tool)
    graph.add_node("retrieve_context", nodes.retrieve_context)
    graph.add_node("plan", nodes.plan)
    graph.add_node("execute_tool", nodes.execute_tool)
    graph.add_node("generate_answer", nodes.generate_answer)
    graph.add_node("save_assistant_message", nodes.save_assistant_message)
    graph.add_node("update_memory_summary", nodes.update_memory_summary)

    if mode == "confirmed_tool":
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "retrieve_context")
        graph.add_edge("retrieve_context", "plan")
        graph.add_edge("plan", "execute_tool")
        graph.add_edge("execute_tool", "generate_answer")
    else:
        graph.add_edge(START, "ensure_conversation")
        graph.add_edge("ensure_conversation", "load_context")
        graph.add_edge("load_context", "save_user_message")
        graph.add_edge("save_user_message", "preflight_confirmable_tool")
        graph.add_conditional_edges(
            "preflight_confirmable_tool",
            _route_after_preflight_confirmable_tool,
            {"execute_tool": "execute_tool", "retrieve_context": "retrieve_context"},
        )
        graph.add_edge("retrieve_context", "plan")
        graph.add_conditional_edges(
            "plan",
            _route_after_plan,
            {"execute_tool": "execute_tool", "generate_answer": "generate_answer"},
        )
        graph.add_conditional_edges(
            "execute_tool",
            _route_after_execute_tool,
            {"plan": "plan", "update_memory_summary": "update_memory_summary"},
        )

    graph.add_edge("generate_answer", "save_assistant_message")
    graph.add_edge("save_assistant_message", "update_memory_summary")
    graph.add_edge("update_memory_summary", END)
    return graph.compile()


def _route_after_plan(state: AgentGraphState) -> str:
    plan = state.get("plan")
    if plan is None or plan.no_tool:
        return "generate_answer"
    return "execute_tool"


def _route_after_preflight_confirmable_tool(state: AgentGraphState) -> str:
    plan = state.get("plan")
    if plan is not None and not plan.no_tool and plan.requires_confirmation:
        return "execute_tool"
    return "retrieve_context"


def _route_after_execute_tool(state: AgentGraphState) -> str:
    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return "plan"
    latest = tool_calls[-1]
    result = latest.get("result") if isinstance(latest, dict) else None
    if (
        isinstance(result, dict)
        and latest.get("requires_confirmation") is True
        and result.get("status") == "failed"
        and result.get("error") == "Tool requires confirmation before execution"
    ):
        return "update_memory_summary"
    return "plan"

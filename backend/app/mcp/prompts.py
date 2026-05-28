from __future__ import annotations

DEFAULT_AGENT_PROMPT = {
    "name": "agent_default",
    "description": "Default AgentDemo assistant prompt.",
    "messages": [
        {
            "role": "system",
            "content": "You are a local personal AI Agent. Answer clearly and practically.",
        }
    ],
}

DOCUMENT_SUMMARY_PROMPT = {
    "name": "document_summary",
    "description": "Summarize a document with concise evidence-backed bullets.",
    "messages": [
        {
            "role": "user",
            "content": "Summarize the provided document and cite the most relevant passages.",
        }
    ],
}

TOOL_PLANNING_PROMPT = {
    "name": "tool_planning",
    "description": "Choose whether a tool should be called for the current task.",
    "messages": [
        {
            "role": "user",
            "content": "Decide whether the task needs a tool and return a structured plan.",
        }
    ],
}

LOCAL_PROMPTS = [DEFAULT_AGENT_PROMPT, DOCUMENT_SUMMARY_PROMPT, TOOL_PLANNING_PROMPT]

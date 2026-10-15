"""Bound model input by characters without network-dependent tokenization.

This is an explicit memory/payload budget, not a model-specific token estimate.
The current request is kept intact. Recent history and the most recent tool
observations take precedence when supplemental material must be shortened.
"""

TRUNCATED = "\n[Context truncated to fit the runtime budget.]"


def fit_context(
    prompt: str, history: list[dict[str, str]], context: list[str], max_chars: int,
) -> tuple[list[dict[str, str]], list[str], dict[str, int]]:
    if len(prompt) > max_chars:
        raise ValueError("Current message exceeds the runtime context budget")
    remaining = max_chars - len(prompt)
    history_budget = remaining // 2 if context else remaining
    kept_history: list[dict[str, str]] = []
    for item in reversed(history):
        content = item.get("content", "")
        if len(content) > history_budget:
            break  # Keep a contiguous recent window, not disconnected older facts.
        kept_history.append(item)
        history_budget -= len(content)
    kept_history.reverse()
    history_chars = sum(len(item["content"]) for item in kept_history)
    remaining -= history_chars
    kept_context: list[str] = []
    kept_source_chars = 0
    for text in reversed(context):
        if len(text) <= remaining:
            kept_context.append(text)
            kept_source_chars += len(text)
            remaining -= len(text)
        elif remaining > len(TRUNCATED):
            kept_context.append(text[:remaining - len(TRUNCATED)] + TRUNCATED)
            kept_source_chars += remaining - len(TRUNCATED)
            remaining = 0
        if remaining == 0:
            break
    kept_context.reverse()
    return kept_history, kept_context, {
        "max_chars": max_chars,
        "input_chars": len(prompt) + history_chars + sum(map(len, kept_context)),
        "dropped_history_messages": len(history) - len(kept_history),
        "omitted_context_chars": sum(map(len, context)) - kept_source_chars,
    }

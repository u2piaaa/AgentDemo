import pytest

from app.agent.context_budget import TRUNCATED, fit_context


def test_budget_preserves_current_request_and_recent_contiguous_history():
    history = [
        {"role": "user", "content": "old" * 100},
        {"role": "assistant", "content": "recent reply"},
        {"role": "user", "content": "recent request"},
    ]
    kept, context, stats = fit_context("current", history, ["tool result"], 100)
    assert kept == history[1:]
    assert context == ["tool result"]
    assert stats["dropped_history_messages"] == 1
    assert stats["input_chars"] == 44


def test_latest_tool_observation_takes_priority_and_truncation_is_explicit():
    kept, context, stats = fit_context("current", [], ["older fact", "latest " + "x" * 200], 100)
    assert kept == []
    assert len(context) == 1
    assert context[0].startswith("latest ")
    assert context[0].endswith(TRUNCATED)
    assert stats["input_chars"] == 100
    assert stats["omitted_context_chars"] == 217 - (93 - len(TRUNCATED))


def test_budget_does_not_select_disconnected_older_history():
    history = [{"role": "user", "content": "short"}, {"role": "assistant", "content": "x" * 200}]
    kept, _, stats = fit_context("current", history, [], 100)
    assert kept == []
    assert stats["dropped_history_messages"] == 2


@pytest.mark.parametrize("budget", [7, 20, 50, 100, 1000])
def test_unicode_and_small_budgets_never_exceed_character_limit(budget):
    _, context, stats = fit_context("current", [], ["中文" * 100, "tool result" * 200], budget)
    assert len("current") + sum(map(len, context)) <= budget
    assert stats["input_chars"] <= budget
    assert stats["omitted_context_chars"] >= 0


def test_oversized_current_request_is_rejected_without_silent_truncation():
    with pytest.raises(ValueError, match="Current message exceeds"):
        fit_context("too long", [], [], 3)

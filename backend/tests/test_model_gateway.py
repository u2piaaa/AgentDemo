from app.services.model_gateway import ModelGateway


def test_routes_low_risk_summary_to_local_model() -> None:
    route = ModelGateway().route("summary", "short text")

    assert route.provider == "deepseek"
    assert route.model_name == "deepseek-v4-pro"


def test_routes_default_to_deepseek_model() -> None:
    route = ModelGateway().route("conversation", "explain an architecture")

    assert route.provider == "deepseek"


def test_build_messages_includes_conversation_history_before_current_prompt() -> None:
    messages = ModelGateway()._build_messages(
        prompt="What is my preferred editor?",
        context=[],
        history=[
            {"role": "user", "content": "Remember that I use VS Code."},
            {"role": "assistant", "content": "Got it."},
        ],
    )

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == "Remember that I use VS Code."
    assert messages[-1]["content"] == "What is my preferred editor?"


def test_build_messages_keeps_knowledge_context_on_current_prompt() -> None:
    messages = ModelGateway()._build_messages(
        prompt="Summarize it.",
        context=["Project notes"],
        history=[{"role": "assistant", "content": "Previous answer"}],
    )

    assert "Use the current conversation history as short-term memory" in messages[0]["content"]
    assert messages[1] == {"role": "assistant", "content": "Previous answer"}
    assert "Supplemental knowledge context, which may be incomplete:\nProject notes" in messages[-1][
        "content"
    ]
    assert "Current user message:\nSummarize it." in messages[-1]["content"]
    assert "Use conversation history above for remembered facts" in messages[-1]["content"]


def test_build_messages_tells_model_history_beats_unrelated_retrieval() -> None:
    messages = ModelGateway()._build_messages(
        prompt="它指的是什么？",
        context=["This project uses PostgreSQL and pgvector."],
        history=[
            {"role": "user", "content": "请记住：我的项目代号叫 Aurora。"},
            {"role": "assistant", "content": "已记下。"},
            {"role": "user", "content": "接下来讨论这个项目时，“它”都指 Aurora。"},
        ],
    )

    assert messages[1]["content"] == "请记住：我的项目代号叫 Aurora。"
    assert messages[3]["content"] == "接下来讨论这个项目时，“它”都指 Aurora。"
    assert "prefer the conversation history" in messages[0]["content"]
    assert "supplemental knowledge context only when it is relevant" in messages[-1]["content"]

from uuid import uuid4

import pytest

from app.agent.runtime import AgentRuntime
from app.core.config import get_settings
from app.models.conversation import Conversation, Message
from app.schemas import ChatRequest


class EmptyResult:
    def all(self) -> list:
        return []

    def scalars(self):
        return self


class FakeSession:
    def __init__(self) -> None:
        self.items = []
        self.commit_count = 0

    async def get(self, model, item_id):
        return None

    def add(self, item) -> None:
        self.items.append(item)

    async def flush(self) -> None:
        for item in self.items:
            if isinstance(item, Conversation):
                item.id = item.id

    async def commit(self) -> None:
        self.commit_count += 1
        return None

    async def execute(self, statement):
        return EmptyResult()


@pytest.mark.asyncio
async def test_agent_runtime_streams_live_reply() -> None:
    if not get_settings().deepseek_api_key:
        pytest.skip("DeepSeek API key is not configured")
    runtime = AgentRuntime(session=FakeSession(), plugin_registry=None)  # type: ignore[arg-type]

    events = []
    async for event in runtime.stream(ChatRequest(message="Reply with exactly: agent ok")):
        events.append(event)
        if event["event"] == "done":
            break

    assert any(event["event"] == "token" for event in events)
    assert events[-1]["event"] == "done"


class ScalarResult:
    def __init__(self, items: list) -> None:
        self.items = items

    def all(self) -> list:
        return self.items

    def scalars(self):
        return self


class HistorySession(FakeSession):
    def __init__(self, history: list[Message]) -> None:
        super().__init__()
        self.history = history

    async def execute(self, statement):
        return ScalarResult(self.history)


@pytest.mark.asyncio
async def test_agent_runtime_loads_recent_history_before_current_message() -> None:
    conversation = Conversation(id=uuid4(), title="Memory")
    history = [
        Message(conversation_id=conversation.id, role="assistant", content="Nice to meet you, Lin."),
        Message(conversation_id=conversation.id, role="user", content="My name is Lin."),
    ]
    session = HistorySession(history)
    runtime = AgentRuntime(session=session, plugin_registry=None)  # type: ignore[arg-type]

    loaded_history = await runtime._load_recent_history(conversation.id)

    assert loaded_history == [
        {"role": "user", "content": "My name is Lin."},
        {"role": "assistant", "content": "Nice to meet you, Lin."},
    ]


@pytest.mark.asyncio
async def test_agent_runtime_auto_titles_new_empty_conversation() -> None:
    session = FakeSession()
    runtime = AgentRuntime(session=session, plugin_registry=None)  # type: ignore[arg-type]
    conversation = Conversation(id=uuid4(), title="New conversation")

    await runtime._auto_title_conversation(
        conversation,
        "请记住：我的项目代号叫 Aurora，首选数据库是 PostgreSQL。",
        history=[],
    )

    assert conversation.title == "我的项目代号叫 Aurora，首选数据库是 PostgreSQL"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_agent_runtime_auto_title_keeps_custom_titles() -> None:
    session = FakeSession()
    runtime = AgentRuntime(session=session, plugin_registry=None)  # type: ignore[arg-type]
    conversation = Conversation(id=uuid4(), title="Custom title")

    await runtime._auto_title_conversation(conversation, "A new topic", history=[])

    assert conversation.title == "Custom title"
    assert session.commit_count == 0

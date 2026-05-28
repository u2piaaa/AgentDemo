from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import tools
from app.api.routes.auth import get_current_user
from app.schemas import ToolManifestRead, ToolRunResponse


class FakeTool:
    manifest = SimpleNamespace(enabled=True)

    def to_read_model(self) -> ToolManifestRead:
        return ToolManifestRead(
            name="read_file",
            description="Read a file.",
            permission="read",
            enabled=True,
            parameters={"type": "object"},
            timeout_seconds=10,
        )


class FakeRegistry:
    def __init__(self) -> None:
        self.tool = FakeTool()

    def list_tools(self) -> list[FakeTool]:
        return [self.tool]

    def get(self, tool_name: str) -> FakeTool | None:
        if tool_name == "read_file":
            return self.tool
        return None


class FakeToolExecutor:
    async def run(self, tool: FakeTool, arguments: dict, **kwargs) -> ToolRunResponse:
        return ToolRunResponse(tool_name="read_file", duration_ms=1, output={"ok": True})


def make_client(authenticated: bool, monkeypatch) -> TestClient:
    app = FastAPI()
    app.state.tool_registry = FakeRegistry()
    app.include_router(tools.router, prefix="/api")
    monkeypatch.setattr(tools, "ToolExecutor", lambda: FakeToolExecutor())

    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    return TestClient(app)


def test_list_tools_requires_login(monkeypatch) -> None:
    client = make_client(authenticated=False, monkeypatch=monkeypatch)

    response = client.get("/api/tools")

    assert response.status_code == 401


def test_run_tool_requires_login(monkeypatch) -> None:
    client = make_client(authenticated=False, monkeypatch=monkeypatch)

    response = client.post("/api/tools/read_file/run", json={"arguments": {}})

    assert response.status_code == 401


def test_authenticated_user_can_list_and_run_tools(monkeypatch) -> None:
    client = make_client(authenticated=True, monkeypatch=monkeypatch)

    list_response = client.get("/api/tools")
    run_response = client.post("/api/tools/read_file/run", json={"arguments": {}})

    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "read_file"
    assert run_response.status_code == 200
    assert run_response.json()["output"] == {"ok": True}

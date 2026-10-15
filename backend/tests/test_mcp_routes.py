from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import mcp
from app.api.routes.auth import get_current_user
from app.mcp.client import McpClientManager
from app.mcp.config import McpConfig, McpServerConfig
from app.mcp.registry import UnifiedToolRegistry
from app.services.plugin_registry import PluginRegistry


def make_client(tmp_path, authenticated):
    app = FastAPI()
    app.state.mcp_client = McpClientManager(
        McpConfig(
            servers=[
                McpServerConfig(
                    name="static",
                    tools=[{"name": "example", "inputSchema": {"type": "object"}}],
                )
            ]
        )
    )
    app.state.tool_registry = UnifiedToolRegistry(PluginRegistry(tmp_path), app.state.mcp_client)
    app.include_router(mcp.router, prefix="/api")
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    return TestClient(app)


def test_refresh_requires_authentication(tmp_path):
    client = make_client(tmp_path, False)
    assert client.post("/api/mcp/refresh").status_code == 401


def test_static_catalog_is_not_reported_as_connected_and_refreshes(tmp_path):
    client = make_client(tmp_path, True)
    response = client.post("/api/mcp/refresh")
    assert response.status_code == 200
    server = response.json()[0]
    assert server["status"] == "configured"
    assert server["registered_tool_count"] == 1
    assert server["protocol_version"] is None
    client.app.state.mcp_client.config.servers[0].tools = []
    assert client.post("/api/mcp/refresh").json()[0]["registered_tool_count"] == 0

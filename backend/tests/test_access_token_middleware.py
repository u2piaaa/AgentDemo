import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.api.routes import auth
from app.core.config import get_settings
from app.main import AccessTokenMiddleware


def make_client(monkeypatch, agent_access_token: str = "secret") -> TestClient:
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(agent_access_token=agent_access_token),
    )
    app = FastAPI()
    app.add_middleware(AccessTokenMiddleware)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/health/ready")
    async def readiness() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/auth/check")
    async def auth_check() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/protected")
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_access_token_middleware_allows_auth_and_health_without_token(monkeypatch) -> None:
    client = make_client(monkeypatch)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health/ready").status_code == 200
    assert client.post("/api/auth/check").status_code == 200


def test_access_token_middleware_rejects_missing_or_wrong_token(monkeypatch) -> None:
    client = make_client(monkeypatch)

    assert client.get("/api/protected").status_code == 401
    assert client.get("/api/protected", headers={"x-agent-access-token": "wrong"}).status_code == 401


def test_access_token_middleware_does_not_allow_forged_bearer(monkeypatch) -> None:
    client = make_client(monkeypatch)

    response = client.get("/api/protected", headers={"Authorization": "Bearer forged"})

    assert response.status_code == 401


def test_access_token_middleware_allows_valid_access_token(monkeypatch) -> None:
    client = make_client(monkeypatch)

    response = client.get("/api/protected", headers={"x-agent-access-token": "secret"})

    assert response.status_code == 200


def test_auth_status_reports_access_token_required(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ACCESS_TOKEN", "secret")
    get_settings.cache_clear()

    assert asyncio.run(auth.auth_status()) == {"required": True}
    get_settings.cache_clear()


def test_auth_status_reports_access_token_not_required(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ACCESS_TOKEN", "")
    get_settings.cache_clear()

    assert asyncio.run(auth.auth_status()) == {"required": False}
    get_settings.cache_clear()

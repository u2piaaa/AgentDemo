import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.api.routes.health import readiness
from app.core.config import Settings, get_settings
from app.db import database


def test_default_cors_allows_only_loopback_and_explicit_origins(monkeypatch):
    settings = Settings(_env_file=None, cors_origins=["https://agent.example"])
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    client = TestClient(main.create_app())
    for origin, expected in [
        ("http://localhost:5173", 200), ("http://127.0.0.1:5174", 200),
        ("http://[::1]:5173", 200), ("https://agent.example", 200),
        ("https://unrelated.example", 400), ("http://localhost.evil.example", 400),
    ]:
        response = client.options("/api/auth/login", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        })
        assert response.status_code == expected


@pytest.mark.asyncio
async def test_readiness_deadline_and_schema_mismatch(monkeypatch):
    monkeypatch.setattr(get_settings(), "health_timeout_seconds", 0.05)

    class Hung:
        async def execute(self, statement):
            await asyncio.Event().wait()

    response = await asyncio.wait_for(readiness(Hung()), timeout=1)
    assert response.status_code == 503

    class Outdated:
        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: "0009_task_fencing")

    response = await readiness(Outdated())
    assert response.status_code == 503 and json.loads(response.body)["schema"] == "outdated"


@pytest.mark.asyncio
async def test_unavailable_connection_returns_generic_503_without_credentials(monkeypatch):
    class Offline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def connection(self):
            raise OSError("secret database connection details")

    monkeypatch.setattr(database, "SessionLocal", Offline)
    with pytest.raises(HTTPException) as failure:
        await anext(database.get_session())
    assert failure.value.status_code == 503
    assert "secret" not in failure.value.detail
    assert failure.value.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_route_timeout_is_not_mislabelled_as_database_failure(monkeypatch):
    class Online:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def connection(self):
            return None

    monkeypatch.setattr(database, "SessionLocal", Online)
    dependency = database.get_session()
    await anext(dependency)
    with pytest.raises(TimeoutError, match="provider"):
        await dependency.athrow(TimeoutError("provider"))

import json

import pytest
from starlette.responses import JSONResponse

from app.api.routes.health import readiness


class ReadySession:
    async def execute(self, statement):
        assert str(statement) == "SELECT 1"


class UnavailableSession:
    async def execute(self, statement):
        raise OSError("database is offline")


@pytest.mark.asyncio
async def test_readiness_reports_database_available() -> None:
    response = await readiness(ReadySession())  # type: ignore[arg-type]

    assert response == {"status": "ready", "database": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_is_unavailable() -> None:
    response = await readiness(UnavailableSession())  # type: ignore[arg-type]

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "unavailable",
        "database": "unavailable",
    }

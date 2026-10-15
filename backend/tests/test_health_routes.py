import json

import pytest
from starlette.responses import JSONResponse

from app.api.routes.health import readiness
from app.api.routes.health import REQUIRED_SCHEMA


class ReadySession:
    async def execute(self, statement):
        assert str(statement) == "SELECT version_num FROM alembic_version"
        return self

    def scalar_one_or_none(self):
        return REQUIRED_SCHEMA


class UnavailableSession:
    async def execute(self, statement):
        raise OSError("database is offline")


@pytest.mark.asyncio
async def test_readiness_reports_database_available() -> None:
    response = await readiness(ReadySession())  # type: ignore[arg-type]

    assert response == {"status": "ready", "database": "ok", "schema": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_is_unavailable() -> None:
    response = await readiness(UnavailableSession())  # type: ignore[arg-type]

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "unavailable",
        "database": "unavailable",
    }

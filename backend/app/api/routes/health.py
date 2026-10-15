from fastapi import APIRouter, Depends
import asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.db.database import get_health_session
from app.core.config import get_settings

REQUIRED_SCHEMA = "0010_auth_sessions"

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def readiness(session: AsyncSession = Depends(get_health_session)) -> dict[str, str] | JSONResponse:
    try:
        async with asyncio.timeout(get_settings().health_timeout_seconds):
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            if result.scalar_one_or_none() != REQUIRED_SCHEMA:
                return JSONResponse({"status": "unavailable", "database": "ok", "schema": "outdated"}, status_code=503)
    except (OSError, SQLAlchemyError, TimeoutError):
        return JSONResponse(
            {"status": "unavailable", "database": "unavailable"},
            status_code=503,
        )
    return {"status": "ready", "database": "ok", "schema": "ok"}

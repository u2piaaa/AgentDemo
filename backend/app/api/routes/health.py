from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.db.database import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def readiness(session: AsyncSession = Depends(get_session)) -> dict[str, str] | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError):
        return JSONResponse(
            {"status": "unavailable", "database": "unavailable"},
            status_code=503,
        )
    return {"status": "ready", "database": "ok"}

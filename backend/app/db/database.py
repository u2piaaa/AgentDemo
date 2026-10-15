from collections.abc import AsyncIterator
import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url, pool_pre_ping=True, pool_timeout=settings.database_connect_timeout_seconds,
    connect_args={"timeout": settings.database_connect_timeout_seconds,
                  "command_timeout": settings.database_command_timeout_seconds},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            async with asyncio.timeout(settings.database_connect_timeout_seconds):
                await session.connection()
        except (OSError, SQLAlchemyError, TimeoutError) as exc:
            raise database_unavailable(exc) from exc
        try:
            yield session
        except SQLAlchemyError as exc:
            raise database_unavailable(exc) from exc


def database_unavailable(exc: Exception) -> HTTPException:
    # Never expose SQL, connection credentials or authentication values.
    logging.getLogger(__name__).warning("Database request unavailable: %s", type(exc).__name__)
    return HTTPException(503, "Database is temporarily unavailable. Please retry.",
                         headers={"Retry-After": "5"})


async def get_health_session() -> AsyncIterator[AsyncSession]:
    # Readiness owns its stricter deadline, including connection checkout.
    async with SessionLocal() as session:
        yield session

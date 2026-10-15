"""Shared fixed-window admission limits before expensive password verification."""

import hashlib
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import AuthRateLimit


async def admit_auth_request(session: AsyncSession, request: Request, username: str) -> None:
    settings = get_settings()
    # Evaluate time inside PostgreSQL, including after waiting for a bucket lock.
    # Worker start times and clocks can differ from the request that opened the window.
    now = func.clock_timestamp()
    ip = request.client.host if request.client else "unknown"
    # Do not parse client-supplied forwarding headers. Proxy trust belongs in ASGI configuration.
    buckets = [(f"ip:{ip}", settings.auth_rate_ip_limit),
               (f"account:{ip}:{username.casefold()}", settings.auth_rate_account_limit)]
    retry_after = 0
    for identity, limit in buckets:
        key = hashlib.sha256(identity.encode()).hexdigest()
        expired = AuthRateLimit.reset_at <= now
        statement = insert(AuthRateLimit).values(
            key=key, count=1, reset_at=now + timedelta(seconds=settings.auth_rate_window_seconds),
        ).on_conflict_do_update(index_elements=[AuthRateLimit.key], set_={
            "count": case((expired, 1), else_=AuthRateLimit.count + 1),
            "reset_at": case((expired, now + timedelta(seconds=settings.auth_rate_window_seconds)),
                             else_=AuthRateLimit.reset_at),
        }).returning(AuthRateLimit.count,
                     func.ceil(func.extract("epoch", AuthRateLimit.reset_at - now)))
        count, remaining_seconds = (await session.execute(statement)).one()
        if count > limit:
            retry_after = max(1, min(settings.auth_rate_window_seconds, int(remaining_seconds)))
            break
    # Bounded cleanup; no raw usernames/IPs/tokens are retained in this table.
    await session.execute(delete(AuthRateLimit).where(AuthRateLimit.key.in_(
        select(AuthRateLimit.key).where(AuthRateLimit.reset_at < now - timedelta(hours=1))
        .order_by(AuthRateLimit.reset_at).limit(100).with_for_update(skip_locked=True)
    )))
    await session.commit()  # failed logins must not roll admission counters back
    if retry_after:
        raise HTTPException(429, "Too many sign-in attempts. Try again shortly.",
                            headers={"Retry-After": str(max(retry_after, 1))})

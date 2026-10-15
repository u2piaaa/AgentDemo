from typing import Annotated
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_session_token, hash_password, hash_session_token, password_work,
    secrets_equal, verify_and_rehash,
)
from app.db.database import get_session
from app.models.user import User, UserSession
from app.schemas import AuthCredentials, AuthResponse, RegisterCredentials, UserRead
from app.services.auth_limits import admit_auth_request

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status() -> dict[str, bool]:
    return {"required": bool(get_settings().agent_access_token)}


@router.post("/check")
async def auth_check(request: Request) -> dict[str, bool]:
    expected = get_settings().agent_access_token
    if not expected:
        return {"ok": True}
    return {"ok": secrets_equal(request.headers.get("x-agent-access-token"), expected)}


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterCredentials,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    username = payload.username.strip()
    await admit_auth_request(session, request, username)
    user = User(
        username=username,
        username_normalized=username.casefold(),
        password_hash=await password_work(hash_password, payload.password),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken") from exc

    token, expires_at = await create_user_session(session, user)
    await session.commit()
    await session.refresh(user)
    return AuthResponse(token=token, user=UserRead.model_validate(user), expires_at=expires_at)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: AuthCredentials,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    username_normalized = payload.username.strip().casefold()
    await admit_auth_request(session, request, username_normalized)
    result = await session.execute(
        select(User).where(User.username_normalized == username_normalized)
    )
    user = result.scalar_one_or_none()
    stored_hash = user.password_hash if user is not None else None
    await session.commit()  # release the read connection while hashing off-thread
    valid, replacement = await password_work(verify_and_rehash, payload.password, stored_hash)
    if user is None or not valid:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Re-read under lock: an overlapping password change must not create a session
    # using a credential that was replaced while memory-hard verification ran.
    user = await session.get(User, user.id, with_for_update=True, populate_existing=True)
    if user is None or user.password_hash != stored_hash:
        raise HTTPException(status_code=401, detail="Credentials changed; please sign in again")
    if replacement is not None:
        user.password_hash = replacement
    token, expires_at = await create_user_session(session, user)
    await session.commit()
    return AuthResponse(token=token, user=UserRead.model_validate(user), expires_at=expires_at)


async def create_user_session(session: AsyncSession, user: User) -> tuple[str, datetime]:
    # User row lock serializes the session cap with concurrent login/logout-all.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    settings, now = get_settings(), datetime.now(UTC)
    active_ids = (await session.scalars(select(UserSession.id).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now,
    ).order_by(UserSession.created_at.desc(), UserSession.id.desc()))).all()
    if len(active_ids) >= settings.auth_max_sessions:
        await session.execute(update(UserSession).where(UserSession.id.in_(
            active_ids[settings.auth_max_sessions - 1:]
        )).values(revoked_at=now))
    await session.execute(delete(UserSession).where(
        UserSession.user_id == user.id, UserSession.expires_at < now - timedelta(days=30),
    ))
    token = create_session_token()
    expires_at = now + timedelta(seconds=settings.auth_session_ttl_seconds)
    session.add(UserSession(user_id=user.id, token_hash=hash_session_token(token),
                            expires_at=expires_at, last_seen_at=now))
    return token, expires_at


async def require_session_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.removeprefix("Bearer ").strip()
    if not 32 <= len(token) <= 128:
        raise HTTPException(status_code=401, detail="Authentication required")
    return token


async def get_current_user(
    token: Annotated[str, Depends(require_session_token)],
    session: AsyncSession = Depends(get_session),
) -> User:

    now = datetime.now(UTC)
    result = await session.execute(select(User, UserSession).join(UserSession).where(
        UserSession.token_hash == hash_session_token(token), UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
        UserSession.last_seen_at > now - timedelta(seconds=get_settings().auth_session_idle_seconds),
    ))
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user, user_session = row
    if user_session.last_seen_at < now - timedelta(seconds=60):
        # Conditional write cannot resurrect a session concurrently revoked by logout.
        await session.execute(update(UserSession).where(
            UserSession.id == user_session.id, UserSession.revoked_at.is_(None),
        ).values(last_seen_at=now))
        await session.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser) -> User:
    return current_user


@router.post("/logout", status_code=204)
async def logout(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if 32 <= len(token) <= 128:
            await session.execute(update(UserSession).where(
                UserSession.token_hash == hash_session_token(token), UserSession.revoked_at.is_(None),
            ).values(revoked_at=datetime.now(UTC)))
            await session.commit()
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
async def logout_all(current_user: CurrentUser, session: AsyncSession = Depends(get_session)) -> Response:
    await session.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    await session.execute(update(UserSession).where(
        UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(UTC)))
    await session.commit()
    return Response(status_code=204)

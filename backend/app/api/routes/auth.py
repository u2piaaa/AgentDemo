from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_session_token, hash_password, hash_session_token, verify_password
from app.db.database import get_session
from app.models.user import User, UserSession
from app.schemas import AuthCredentials, AuthResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status() -> dict[str, bool]:
    return {"required": True}


@router.post("/check")
async def auth_check(request: Request) -> dict[str, bool]:
    expected = get_settings().agent_access_token
    if not expected:
        return {"ok": True}
    return {"ok": request.headers.get("x-agent-access-token") == expected}


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: AuthCredentials,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    username = payload.username.strip()
    user = User(
        username=username,
        username_normalized=username.casefold(),
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken") from exc

    token = await create_user_session(session, user)
    await session.commit()
    await session.refresh(user)
    return AuthResponse(token=token, user=UserRead.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: AuthCredentials,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    username_normalized = payload.username.strip().casefold()
    result = await session.execute(
        select(User).where(User.username_normalized == username_normalized)
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = await create_user_session(session, user)
    await session.commit()
    return AuthResponse(token=token, user=UserRead.model_validate(user))


async def create_user_session(session: AsyncSession, user: User) -> str:
    token = create_session_token()
    session.add(UserSession(user_id=user.id, token_hash=hash_session_token(token)))
    return token


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await session.execute(
        select(User).join(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser) -> User:
    return current_user

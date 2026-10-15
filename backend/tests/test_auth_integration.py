"""Real PostgreSQL authentication, revocation, migration and concurrent limits."""

import asyncio
import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid import uuid4

from app.core.config import get_settings
from app.core.security import hash_session_token
from app.db.database import get_health_session, get_session
from app.main import create_app
from app.models.user import AuthRateLimit, User, UserSession

pytestmark = pytest.mark.integration


@pytest.fixture
async def auth_app(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"auth_{uuid4().hex[:18]}"
    ip = f"fixture-{uuid4().hex}"
    names = set()

    def credentials(suffix=""):
        name = prefix + suffix
        names.add(name)
        return {"username": name, "password": "Synthetic test passphrase 2026!"}
    credentials.client_ip = ip

    async def session_dependency():
        async with sessions() as session:
            yield session

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_token", "")
    monkeypatch.setattr(settings, "auth_rate_ip_limit", 100)
    monkeypatch.setattr(settings, "auth_rate_account_limit", 100)
    monkeypatch.setattr(settings, "auth_max_sessions", 5)
    app = create_app()
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_health_session] = session_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1234)),
                                     base_url="http://fixture") as client:
            yield client, sessions, credentials, settings
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.username_normalized.in_(names)))
            keys = [hashlib.sha256(f"ip:{ip}".encode()).hexdigest(), *[
                hashlib.sha256(f"account:{ip}:{name}".encode()).hexdigest() for name in names
            ]]
            await session.execute(delete(AuthRateLimit).where(AuthRateLimit.key.in_(keys)))
            await session.commit()
        await engine.dispose()


def bearer(response):
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.mark.asyncio
async def test_register_login_revoke_and_owner_isolation(auth_app):
    client, sessions, credentials, _ = auth_app
    data = credentials()
    registered = await client.post("/api/auth/register", json=data)
    assert registered.status_code == 201 and registered.json()["expires_at"]
    assert registered.headers["cache-control"] == "no-store"
    headers = bearer(registered)
    assert (await client.get("/api/auth/me", headers=headers)).json()["username"] == data["username"]
    async with sessions() as session:
        user = (await session.scalars(select(User).where(User.username == data["username"]))).one()
        assert user.password_hash.startswith("$argon2id$")
        token = (await session.scalars(select(UserSession).where(UserSession.user_id == user.id))).one()
        assert token.token_hash == hash_session_token(registered.json()["token"])
        assert registered.json()["token"] not in token.token_hash
    other = await client.post("/api/auth/register", json=credentials("_two"))
    conversation = await client.post("/api/conversations", headers=headers, json={"title": "Synthetic private conversation"})
    assert conversation.status_code == 200
    denied = await client.get(f"/api/conversations/{conversation.json()['id']}/messages", headers=bearer(other))
    assert denied.status_code == 404
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(other))).status_code == 200
    login = await client.post("/api/auth/login", json=data)
    assert login.status_code == 200 and login.json()["token"] != registered.json()["token"]


@pytest.mark.asyncio
async def test_real_legacy_login_migrates_hash_without_password_reset(auth_app):
    client, sessions, credentials, _ = auth_app
    data = credentials()
    data["password"] = "oldpass"
    salt = b"synthetic-salt16"
    digest = hashlib.pbkdf2_hmac("sha256", data["password"].encode(), salt, 260000)
    old = f"pbkdf2_sha256$260000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"
    async with sessions() as session:
        session.add(User(username=data["username"], username_normalized=data["username"], password_hash=old))
        await session.commit()
    failed = await client.post("/api/auth/login", json={**data, "password": "wrong"})
    assert failed.status_code == 401
    async with sessions() as session:
        user = (await session.scalars(select(User).where(User.username == data["username"]))).one()
        assert user.password_hash == old
    logged_in = await client.post("/api/auth/login", json=data)
    assert logged_in.status_code == 200
    async with sessions() as session:
        user = (await session.scalars(select(User).where(User.username == data["username"]))).one()
        assert user.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_field", ["expires_at", "last_seen_at"])
async def test_server_enforces_absolute_and_idle_expiry(auth_app, expired_field):
    client, sessions, credentials, _ = auth_app
    response = await client.post("/api/auth/register", json=credentials())
    async with sessions() as session:
        await session.execute(update(UserSession).where(
            UserSession.token_hash == hash_session_token(response.json()["token"]),
        ).values(**{expired_field: datetime.now(UTC) - timedelta(days=2)}))
        await session.commit()
    assert (await client.get("/api/auth/me", headers=bearer(response))).status_code == 401


@pytest.mark.asyncio
async def test_concurrent_logins_enforce_cap_and_logout_all(auth_app, monkeypatch):
    client, sessions, credentials, settings = auth_app
    monkeypatch.setattr(settings, "auth_max_sessions", 2)
    data = credentials()
    await client.post("/api/auth/register", json=data)
    responses = await asyncio.gather(*[client.post("/api/auth/login", json=data) for _ in range(4)])
    assert all(response.status_code == 200 for response in responses)
    validity = [await client.get("/api/auth/me", headers=bearer(response)) for response in responses]
    assert sum(response.status_code == 200 for response in validity) == 2
    current = next(response for response, valid in zip(responses, validity, strict=True) if valid.status_code == 200)
    assert (await client.post("/api/auth/logout-all", headers=bearer(current))).status_code == 204
    rejected = await asyncio.gather(*[client.get("/api/auth/me", headers=bearer(response)) for response in responses])
    assert all(response.status_code == 401 for response in rejected)


@pytest.mark.asyncio
async def test_parallel_wrong_passwords_share_rate_limits(auth_app, monkeypatch):
    client, _, credentials, settings = auth_app
    monkeypatch.setattr(settings, "auth_rate_account_limit", 3)
    data = credentials("_missing")
    responses = await asyncio.gather(*[client.post("/api/auth/login", json=data) for _ in range(7)])
    assert sorted(response.status_code for response in responses) == [401, 401, 401, 429, 429, 429, 429]
    limited = next(response for response in responses if response.status_code == 429)
    assert 1 <= int(limited.headers["retry-after"]) <= 60


@pytest.mark.asyncio
async def test_ip_limit_covers_different_usernames_and_window_resets(auth_app, monkeypatch):
    client, sessions, credentials, settings = auth_app
    monkeypatch.setattr(settings, "auth_rate_ip_limit", 2)
    responses = [await client.post("/api/auth/login", json=credentials(f"_{index}")) for index in range(3)]
    assert [response.status_code for response in responses] == [401, 401, 429]
    # Only mutate this fixture's own rate-limit row.
    async with sessions() as session:
        key = hashlib.sha256(f"ip:{credentials.client_ip}".encode()).hexdigest()
        limited = await session.get(AuthRateLimit, key)
        assert limited.count == 3
        limited.reset_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (await client.post("/api/auth/login", json=credentials("_new"))).status_code == 401


@pytest.mark.asyncio
async def test_access_gate_covers_registration_and_new_password_policy(auth_app, monkeypatch):
    client, _, credentials, settings = auth_app
    monkeypatch.setattr(settings, "agent_access_token", "synthetic-project-token")
    assert (await client.get("/api/auth/status")).json()["required"]
    assert (await client.post("/api/auth/check")).json() == {"ok": False}
    data = credentials()
    assert (await client.post("/api/auth/register", json=data)).status_code == 401
    headers = {"x-agent-access-token": "synthetic-project-token"}
    assert (await client.post("/api/auth/register", headers=headers, json={**data, "password": "short"})).status_code == 422
    response = await client.post("/api/auth/register", headers=headers, json=data)
    assert response.status_code == 201
    assert (await client.post("/api/auth/register", headers=headers, json=data)).status_code == 409


@pytest.mark.asyncio
async def test_current_schema_readiness_uses_real_database(auth_app):
    client, _, _, _ = auth_app
    response = await client.get("/api/health/ready")
    assert response.status_code == 200 and response.json()["schema"] == "ok"

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.mcp_security import (
    McpIdentity,
    assert_workspace_relative_path,
    authorize_mcp_access,
    contains_plaintext_mcp_secret,
    enforce_mcp_tool_policy,
    requires_mcp_confirmation,
    scrub_mcp_config,
    validate_mcp_settings,
)


@pytest.fixture(autouse=True)
def reset_mcp_settings():
    settings = get_settings()
    original = {
        "mcp_access_policy": settings.mcp_access_policy,
        "mcp_remote_enabled": settings.mcp_remote_enabled,
        "mcp_server_bind_host": settings.mcp_server_bind_host,
        "mcp_require_confirmation_by_default": settings.mcp_require_confirmation_by_default,
        "mcp_allowed_transports": list(settings.mcp_allowed_transports),
    }
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


def test_mcp_server_defaults_to_local_bind(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_server_bind_host = "0.0.0.0"
    settings.mcp_remote_enabled = False

    with pytest.raises(RuntimeError):
        validate_mcp_settings(settings)


def test_remote_mcp_access_requires_explicit_enable(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_access_policy = "local-only"
    settings.mcp_remote_enabled = False

    with pytest.raises(HTTPException) as exc_info:
        authorize_mcp_access(McpIdentity(user_id=uuid4()), settings=settings, remote=True)

    assert exc_info.value.status_code == 403


def test_authenticated_mcp_policy_requires_identity(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_access_policy = "authenticated"

    with pytest.raises(HTTPException) as exc_info:
        authorize_mcp_access(McpIdentity(), settings=settings)

    assert exc_info.value.status_code == 401


def test_admin_mcp_policy_requires_admin_identity(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_access_policy = "admin-only"

    with pytest.raises(HTTPException) as exc_info:
        authorize_mcp_access(McpIdentity(user_id=uuid4()), settings=settings)

    assert exc_info.value.status_code == 403


def test_risky_mcp_permission_requires_confirmation(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_access_policy = "authenticated"

    assert requires_mcp_confirmation("network", settings=settings)
    with pytest.raises(HTTPException) as exc_info:
        enforce_mcp_tool_policy(
            permission="network",
            requires_confirmation=False,
            confirmed=False,
            identity=McpIdentity(user_id=uuid4()),
            settings=settings,
        )

    assert exc_info.value.status_code == 409


def test_read_mcp_permission_can_run_after_auth(reset_mcp_settings) -> None:
    settings = reset_mcp_settings
    settings.mcp_access_policy = "authenticated"

    enforce_mcp_tool_policy(
        permission="read",
        requires_confirmation=False,
        confirmed=False,
        identity=McpIdentity(user_id=uuid4()),
        settings=settings,
    )


def test_mcp_workspace_path_must_stay_inside_root(tmp_path: Path) -> None:
    assert assert_workspace_relative_path("docs/readme.md", tmp_path) == (
        tmp_path / "docs" / "readme.md"
    ).resolve()
    with pytest.raises(HTTPException) as exc_info:
        assert_workspace_relative_path("../secret.txt", tmp_path)

    assert exc_info.value.status_code == 403


def test_mcp_config_secret_detection_and_scrub() -> None:
    config = {
        "servers": {
            "private": {
                "command": "python",
                "env": {"API_KEY": "live-key", "SAFE_VALUE": "ok"},
            }
        }
    }

    assert contains_plaintext_mcp_secret(config)
    assert scrub_mcp_config(config)["servers"]["private"]["env"]["API_KEY"] == "***REDACTED***"

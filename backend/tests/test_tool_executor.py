from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.plugin_registry import PluginManifest, RegisteredTool
from app.services.tool_executor import ToolExecutor


def make_tool(parameters: dict, enabled: bool = True) -> RegisteredTool:
    manifest = PluginManifest(
        name="example",
        description="Example tool.",
        parameters=parameters,
        entrypoint="tool.py:run",
        enabled=enabled,
    )
    return RegisteredTool(manifest=manifest, handler=lambda **kwargs: kwargs, base_dir=Path("."))


def validate(arguments: dict, parameters: dict) -> None:
    ToolExecutor()._validate_arguments(make_tool(parameters), arguments)


def test_tool_argument_validation_accepts_valid_payload() -> None:
    validate(
        {"path": "README.md"},
        {
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string", "format": "path"}},
        },
    )


def test_tool_argument_validation_rejects_missing_required() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "Missing required arguments" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_wrong_type() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": 123},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "path must be string" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_unknown_fields() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": "README.md", "extra": True},
            {
                "type": "object",
                "required": ["path"],
                "additionalProperties": False,
                "properties": {"path": {"type": "string"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "Unknown arguments" in str(exc_info.value.detail)


def test_tool_argument_validation_rejects_format_errors() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate(
            {"path": ""},
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string", "format": "path"}},
            },
        )

    assert exc_info.value.status_code == 422
    assert "path must match format path" in str(exc_info.value.detail)

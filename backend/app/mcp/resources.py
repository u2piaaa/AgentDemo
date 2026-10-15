from __future__ import annotations

from typing import Any


def local_resource(uri: str, name: str, text: str, mime_type: str = "text/plain") -> dict[str, Any]:
    return {"uri": uri, "name": name, "mimeType": mime_type, "text": text}


def extract_mcp_resource_text(resource: dict) -> str:
    """Normalize inline and standard MCP resources/read content blocks to text."""

    collected: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in collected:
                collected.append(text)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if isinstance(value, dict):
            for key in ("text", "content"):
                collect(value.get(key))

    collect(resource.get("text"))
    collect(resource.get("content"))
    collect(resource.get("contents"))
    return "\n\n".join(collected)

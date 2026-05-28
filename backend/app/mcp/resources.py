from __future__ import annotations

from typing import Any


def local_resource(uri: str, name: str, text: str, mime_type: str = "text/plain") -> dict[str, Any]:
    return {"uri": uri, "name": name, "mimeType": mime_type, "text": text}

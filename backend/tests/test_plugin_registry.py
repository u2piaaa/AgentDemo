import json
from pathlib import Path

from app.services.plugin_registry import PluginRegistry


def test_loads_local_plugin_manifest(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "echo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "tool.py").write_text(
        "def run(text: str):\n    return {'text': text}\n",
        encoding="utf-8",
    )
    (plugin_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "echo",
                "description": "Echo input.",
                "parameters": {"type": "object", "required": ["text"], "properties": {}},
                "entrypoint": "tool.py:run",
            }
        ),
        encoding="utf-8",
    )

    registry = PluginRegistry(tmp_path / "plugins")
    registry.load()

    assert registry.get("echo") is not None

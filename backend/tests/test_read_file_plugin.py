import importlib.util
from pathlib import Path

import pytest


PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "read_file" / "tool.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("read_file_plugin_under_test", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_file_reads_workspace_readme() -> None:
    plugin = load_plugin()

    result = plugin.run("README.md")

    assert result["path"].endswith("README.md")
    assert result["chars"] > 0
    assert "AgentDemo" in result["content"]


def test_read_file_rejects_workspace_escape() -> None:
    plugin = load_plugin()

    with pytest.raises(ValueError, match="outside workspace"):
        plugin.run("../outside.txt")


def test_read_file_rejects_env_file() -> None:
    plugin = load_plugin()

    with pytest.raises(ValueError, match="protected path segment"):
        plugin.run(".env")

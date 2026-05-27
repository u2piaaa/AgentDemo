import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from app.schemas import ToolManifestRead


class PluginManifest(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str
    parameters: dict[str, Any]
    permission: str = "safe"
    timeout_seconds: int = 30
    entrypoint: str
    enabled: bool = True


@dataclass
class RegisteredTool:
    manifest: PluginManifest
    handler: Callable[..., Any]
    base_dir: Path

    def to_read_model(self) -> ToolManifestRead:
        return ToolManifestRead(
            name=self.manifest.name,
            description=self.manifest.description,
            permission=self.manifest.permission,
            enabled=self.manifest.enabled,
            parameters=self.manifest.parameters,
        )


class PluginRegistry:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self._tools: dict[str, RegisteredTool] = {}

    def load(self) -> None:
        self._tools.clear()
        if not self.plugin_dir.exists():
            return
        for manifest_path in self.plugin_dir.glob("*/manifest.json"):
            try:
                tool = self._load_manifest(manifest_path)
            except (ValidationError, ImportError, AttributeError, OSError, json.JSONDecodeError):
                continue
            self._tools[tool.manifest.name] = tool

    def list_tools(self) -> list[RegisteredTool]:
        return sorted(self._tools.values(), key=lambda item: item.manifest.name)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def _load_manifest(self, manifest_path: Path) -> RegisteredTool:
        manifest = PluginManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        module_path_text, function_name = manifest.entrypoint.split(":", 1)
        module_path = manifest_path.parent / module_path_text
        spec = importlib.util.spec_from_file_location(f"plugin_{manifest.name}", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin module {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, function_name)
        return RegisteredTool(manifest=manifest, handler=handler, base_dir=manifest_path.parent)

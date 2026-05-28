from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_NAMES = {
    ".env",
    ".local",
    ".venv",
    "venv",
    "env",
    "downloads",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
}


def run(path: str, max_entries: int = 100) -> dict:
    target = _resolve_workspace_path(path)
    _ensure_allowed_path(target)
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {target}")
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {target}")
    entries = []
    for item in sorted(target.iterdir(), key=lambda value: value.name.lower())[:max_entries]:
        if FORBIDDEN_NAMES.intersection(item.parts):
            continue
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(WORKSPACE_ROOT)),
                "type": "directory" if item.is_dir() else "file",
            }
        )
    return {"path": str(target), "entries": entries}


def _resolve_workspace_path(path: str) -> Path:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = WORKSPACE_ROOT / requested
    return requested.resolve(strict=False)


def _ensure_allowed_path(target: Path) -> None:
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"Refusing to list outside workspace: {target}") from exc
    forbidden = FORBIDDEN_NAMES.intersection(target.parts)
    if forbidden:
        raise ValueError(f"Refusing to list protected path segment: {sorted(forbidden)[0]}")

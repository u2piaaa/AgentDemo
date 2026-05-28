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


def run(path: str) -> dict[str, str | int]:
    target = _resolve_workspace_path(path)
    _ensure_allowed_path(target)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {target}")
    if not target.is_file():
        raise ValueError(f"Path is not a file: {target}")
    try:
        content = target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not UTF-8 text: {target}") from exc
    except PermissionError as exc:
        raise PermissionError(f"Permission denied reading file: {target}") from exc
    return {
        "path": str(target),
        "chars": len(content),
        "content": content[:8000],
    }


def _resolve_workspace_path(path: str) -> Path:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = WORKSPACE_ROOT / requested
    return requested.resolve(strict=False)


def _ensure_allowed_path(target: Path) -> None:
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"Refusing to read outside workspace: {target}") from exc

    forbidden = FORBIDDEN_NAMES.intersection(target.parts)
    if forbidden:
        blocked = sorted(forbidden)[0]
        raise ValueError(f"Refusing to read protected path segment: {blocked}")

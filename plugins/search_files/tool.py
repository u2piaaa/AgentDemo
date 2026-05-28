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
TEXT_EXTENSIONS = {".md", ".py", ".json", ".toml", ".txt", ".tsx", ".ts", ".css", ".html"}


def run(query: str, path: str = ".", max_results: int = 20) -> dict:
    if not query.strip():
        raise ValueError("Query is required")
    root = _resolve_workspace_path(path)
    _ensure_allowed_path(root)
    if not root.exists():
        raise FileNotFoundError(f"Search path not found: {root}")
    files = [root] if root.is_file() else root.rglob("*")
    results = []
    needle = query.lower()
    for file_path in files:
        if len(results) >= max_results:
            break
        if not file_path.is_file() or file_path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if FORBIDDEN_NAMES.intersection(file_path.parts):
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                results.append(
                    {
                        "path": str(file_path.relative_to(WORKSPACE_ROOT)),
                        "line": line_number,
                        "text": line[:500],
                    }
                )
                break
    return {"query": query, "results": results}


def _resolve_workspace_path(path: str) -> Path:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = WORKSPACE_ROOT / requested
    return requested.resolve(strict=False)


def _ensure_allowed_path(target: Path) -> None:
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"Refusing to search outside workspace: {target}") from exc
    forbidden = FORBIDDEN_NAMES.intersection(target.parts)
    if forbidden:
        raise ValueError(f"Refusing to search protected path segment: {sorted(forbidden)[0]}")

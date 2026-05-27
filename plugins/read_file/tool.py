from pathlib import Path


def run(path: str) -> dict[str, str | int]:
    target = Path(path).expanduser().resolve()
    content = target.read_text(encoding="utf-8")
    return {
        "path": str(target),
        "chars": len(content),
        "content": content[:8000],
    }

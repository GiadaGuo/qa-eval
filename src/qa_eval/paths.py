"""Project-root-relative path helpers for local runtime files."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def safe_path_label(path: str | Path) -> str:
    """Return a non-sensitive label suitable for health responses."""
    resolved = resolve_project_path(path)
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return "<external>"

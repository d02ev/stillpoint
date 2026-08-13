"""Recent-projects history.

A small JSON file (recents.json) with entries for every project that has been
opened or created, most recently used first, capped at a fixed size.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import io, paths

MAX_RECENTS = 12


class RecentsError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def recents_file() -> Path:
    return paths.recents_path()


def _read() -> list:
    data = io.read_json(recents_file())
    if data is None:
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("title"), str) and isinstance(e.get("path"), str)]


def list_recents(limit: int = MAX_RECENTS) -> list[dict]:
    """Return recent projects as [{title, path, modified}], newest first."""
    return [dict(e) for e in _read()[:limit]]


def touch_recent(title: str, directory: Path) -> None:
    """Record an open/create event for a project (move to the top)."""
    if not directory.is_dir():
        raise RecentsError(f"project directory does not exist: {directory}")
    entry = {"title": title, "path": str(directory), "modified": _now_iso()}
    entries = [e for e in _read() if e["path"] != str(directory)]
    entries.insert(0, entry)
    io.atomic_write_json(recents_file(), entries[:MAX_RECENTS])


def remove_recent(directory: Path) -> None:
    """Forget a project (when it's deleted from disk)."""
    entries = [e for e in _read() if e["path"] != str(directory)]
    io.atomic_write_json(recents_file(), entries)

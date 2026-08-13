"""Small file helpers with retry.

Windows tends to hold file handles briefly after a program closes them, and an
auto-save happening exactly then can fail. These helpers retry a few times with
a short delay before giving up.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

_RETRIES = 3
_DELAY_S = 0.15


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically (temp file in the same dir, then rename).

    Keeps crash-resilient, non-corrupt files on disk.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=directory)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except OSError:
                if attempt == _RETRIES - 1:
                    raise
                time.sleep(_DELAY_S)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON data atomically with a stable, sorted key order."""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write_text(path, text)


def read_json(path: Path) -> Any:
    """Read JSON data, returning None if the file is missing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None

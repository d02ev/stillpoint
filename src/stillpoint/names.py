"""Friendly file and project naming.

Filenames on Windows are compared case-insensitively, so a single project name
suffix case matters: 'zen 2.mp4' and 'zen 2.MP4' are the same file. We use
' (2)', ' (3)', ... which is also what most apps do, and keep the original
extension casing.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNWANTED = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Strip characters Windows forbids in filenames and tidy whitespace."""
    cleaned = _UNWANTED.sub("", name)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" .") or "untitled"


def unique_filename(directory: Path, stem: str, extension: str) -> str:
    """Return 'stem.ext', or 'stem (2).ext', ... so the result doesn't exist.

    The search is case-insensitive, matching Windows behaviour.
    """
    directory = directory.resolve()
    taken = {p.name.lower() for p in directory.iterdir() if p.is_file()} if directory.is_dir() else set()
    candidate = f"{sanitize_filename(stem)}{extension}"
    counter = 1
    while candidate.lower() in taken:
        counter += 1
        candidate = f"{sanitize_filename(stem)} ({counter}){extension}"
    return candidate


def project_dir_name(project_title: str) -> str:
    """Directory name for a project, sanitized and capped at 40 chars."""
    return sanitize_filename(project_title)[:40]


def resolve_project_dir(parent: Path, project_title: str) -> Path:
    """Return a non-existent (or reusable) directory path for a project."""
    return parent / project_dir_name(project_title)

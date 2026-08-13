"""Application paths.

Everything resolves the on-disk locations through this module so tests can point
them at temporary directories via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECTS_FOLDER_NAME = "Stillpoint Projects"


def default_projects_dir() -> Path:
    """The folder that holds every project.

    Overridable via STILLPOINT_PROJECTS_DIR (used by tests). Defaults to
    Documents\\Stillpoint Projects.
    """
    override = os.environ.get("STILLPOINT_PROJECTS_DIR")
    if override:
        return Path(override)
    return Path.home() / "Documents" / PROJECTS_FOLDER_NAME


def app_data_dir() -> Path:
    """The app's own data folder (recents history lives here).

    Overridable via STILLPOINT_APPDATA (used by tests). Defaults to
    %APPDATA%\\Stillpoint.
    """
    override = os.environ.get("STILLPOINT_APPDATA")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Stillpoint"
    return Path.home() / ".stillpoint"


def recents_path() -> Path:
    """Path of the recents history file."""
    return app_data_dir() / "recents.json"

import os
import sys
from pathlib import Path

# Ensure the src layout is importable when running pytest via `uv run`.
# The installed editable package already covers this; this import guard is a
# belt-and-braces for direct invocations of pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tkinter as tk

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def tk_root():
    """A single Tk root for all GUI tests.

    Creating and destroying a Tk root per test is flaky on Windows (intermittent
    'Can't find a usable tk.tcl'); one shared root avoids the churn.
    """
    root = tk.Tk()
    root.withdraw()
    yield root
    for child in root.winfo_children():
        child.destroy()
    root.destroy()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point every app-data and projects location at a temp dir.

    Keeps tests from touching the real Documents folder or %APPDATA%.
    """
    monkeypatch.setenv("STILLPOINT_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("STILLPOINT_APPDATA", str(tmp_path / "appdata"))
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)
    os.environ.pop("HOME", None)
    yield

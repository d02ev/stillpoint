"""The Stillpoint application window: owns the root Tk widget and switches
between the home and editor screens."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from .. import dialogs, model as model_mod, paths, recents, theme
from .editor import EditorScreen
from .home import HomeScreen

APP_TITLE = "Stillpoint"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(bg=theme.Palette.background)
        self.root.geometry("1040x680")
        self.root.minsize(900, 560)
        self.project: model_mod.Project | None = None

        self._home = HomeScreen(self)
        self._editor = EditorScreen(self)
        self._current: tk.Frame | None = None

        self.show_home()

    # -- screen management ---------------------------------------------------

    def show_home(self) -> None:
        self.project = None
        if self._current:
            self._current.pack_forget()
        self._home.refresh()
        self._current = self._home
        self._home.pack(fill="both", expand=True)

    def show_editor(self, project: model_mod.Project) -> None:
        self.project = project
        if self._current:
            self._current.pack_forget()
        self._editor.refresh()
        self._current = self._editor
        self._editor.pack(fill="both", expand=True)

    # -- project lifecycle ---------------------------------------------------

    def create_project(self, title: str, directory: Path) -> None:
        from datetime import datetime

        created = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            project = model_mod.new_project(title, directory, created)
        except ValueError as exc:
            dialogs.error("Stillpoint", str(exc), parent=self.root)
            return
        recents.touch_recent(title, directory)
        self.show_editor(project)

    def open_project(self, directory: Path) -> None:
        try:
            project = model_mod.Project.load(directory)
        except Exception as exc:  # noqa: BLE001
            dialogs.error("Stillpoint", f"Could not open the project:\n{exc}", parent=self.root)
            return
        recents.touch_recent(project.title, directory)
        self.show_editor(project)


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()

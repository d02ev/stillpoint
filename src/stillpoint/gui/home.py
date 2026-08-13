"""Home screen: recent projects plus New / Open actions."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from .. import dialogs, names, paths, recents
from .. import theme
from . import workers  # noqa: F401  (kept for parity of imports)

RECENT_WIDTH = 60


class HomeScreen(tk.Frame):
    def __init__(self, app, **kwargs):
        super().__init__(app.root, bg=theme.Palette.background, **kwargs)
        self.app = app
        self._title = tk.Label(
            self, text="Stillpoint", bg=theme.Palette.background,
            fg=theme.Palette.accent, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BIG, "bold"))
        self._title.pack(pady=(theme.PAD_LARGE, theme.PAD_SMALL))
        subtitle = tk.Label(
            self, text="A quiet place to make meditation videos.",
            bg=theme.Palette.background, fg=theme.Palette.text_dim,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        subtitle.pack(pady=(0, theme.PAD_LARGE))

        self._listbox = tk.Listbox(
            self, width=RECENT_WIDTH, height=14,
            bg=theme.Palette.panel, fg=theme.Palette.text,
            selectbackground=theme.Palette.accent_soft,
            selectforeground=theme.Palette.text,
            highlightthickness=1, highlightbackground=theme.Palette.border,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        self._listbox.pack(padx=theme.PAD_LARGE, pady=(0, theme.PAD_SMALL), fill="x")
        self._listbox.bind("<Double-Button-1>", lambda _e: self._open_selected())

        button_row = tk.Frame(self, bg=theme.Palette.background)
        button_row.pack(pady=theme.PAD)
        self._button(button_row, "New Project", self._new_project)
        self._button(button_row, "Open Project...", self._open_existing)
        self._button(button_row, "Remove from list", self._remove_selected)

    def _button(self, parent, text, command) -> None:
        tk.Button(
            parent, text=text, command=command,
            bg=theme.Palette.panel_light, fg=theme.Palette.text,
            activebackground=theme.Palette.panel,
            relief="flat", highlightthickness=1,
            highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        ).pack(side="left", padx=theme.PAD_SMALL)

    def refresh(self) -> None:
        self._listbox.delete(0, "end")
        self._entries = recents.list_recents()
        for entry in self._entries:
            self._listbox.insert("end", f"{entry['title']}   ·   {Path(entry['path']).name}")

    def _new_project(self) -> None:
        title = dialogs.ask_string("New Project", "Give your project a title:", parent=self)
        if not title:
            return
        projects_dir = paths.default_projects_dir()
        projects_dir.mkdir(parents=True, exist_ok=True)
        directory = names.resolve_project_dir(projects_dir, title)
        self.app.create_project(title, directory)

    def _open_selected(self) -> None:
        selection = self._listbox.curselection()
        if not selection:
            return
        entry = self._entries[selection[0]]
        directory = Path(entry["path"])
        if not (directory / "project.json").exists():
            dialogs.error("Stillpoint", "That project file no longer exists.", parent=self)
            recents.remove_recent(directory)
            self.refresh()
            return
        self.app.open_project(directory)

    def _open_existing(self) -> None:
        directory = filedialog.askdirectory(
            parent=self, title="Choose a project folder",
            initialdir=str(paths.default_projects_dir()))
        if directory and (Path(directory) / "project.json").exists():
            self.app.open_project(Path(directory))
        elif directory:
            dialogs.error("Stillpoint", "That folder has no project.json.", parent=self)

    def _remove_selected(self) -> None:
        selection = self._listbox.curselection()
        if not selection:
            return
        entry = self._entries[selection[0]]
        recents.remove_recent(Path(entry["path"]))
        self.refresh()

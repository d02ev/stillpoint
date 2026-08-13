"""The real audio-download panel (spec 003): paste a YouTube link, download
music into the project, and import a track into the background-music channel.

The panel is a thin Tkinter widget. It never touches the model or the disk
itself (Constitution VI): Download starts a :class:`DownloadWorker` and polls
its event queue with ``root.after``, rendering each event verbatim; importing a
listed track calls the ``on_import(filename)`` callback the editor wires.
"""

from __future__ import annotations

import tkinter as tk

from .. import download, theme
from . import icons
from .panels import PANEL_DOWNLOAD, PANEL_TITLES, _PanelFrame
from .workers import DownloadWorker

_PLACEHOLDER = "Paste a YouTube link here"
_POLL_MS = 100


class DownloadPanel(_PanelFrame):
    def __init__(self, master, on_import=None, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_DOWNLOAD], **kwargs)
        self._project = None
        self._on_import = on_import
        self._worker: DownloadWorker | None = None

        self._build_controls()
        self._sync_download_button()

    # -- construction --------------------------------------------------------

    def _build_controls(self) -> None:
        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            self._body, textvariable=self._entry_var,
            bg=theme.Palette.panel_light, fg=theme.Palette.text,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
            relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
        )
        self._entry.insert(0, _PLACEHOLDER)
        self._entry.pack(fill="x", pady=(0, theme.PAD_SMALL))
        self._entry.bind("<FocusIn>", self._on_focus_in)
        self._entry.bind("<KeyRelease>", lambda _e: self._sync_download_button())

        self._download_btn = tk.Button(
            self._body, text="Download",
            image=icons.get_icon("download", color="#FFFFFF"),
            compound="left", command=self._on_download,
            bg=theme.Palette.accent, fg="#FFFFFF", activebackground=theme.Palette.accent_hover,
            activeforeground="#FFFFFF", relief="flat", highlightthickness=0,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"),
        )
        self._download_btn.pack(fill="x", pady=(0, theme.PAD_SMALL))

        self._stop_btn = tk.Button(
            self._body, text="Stop", command=self._on_stop,
            bg=theme.Palette.panel_light, fg=theme.Palette.text,
            activebackground=theme.Palette.accent_soft, activeforeground=theme.Palette.text,
            relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        )

        self._progress = tk.Label(
            self._body, text="", bg=theme.Palette.panel, fg=theme.Palette.text,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE), justify="left", anchor="w",
            wraplength=self.width - theme.PAD * 2,
        )
        self._progress.pack(fill="x", pady=(0, theme.PAD_SMALL))

        tk.Label(self._body, text="Your music", bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold")
                 ).pack(anchor="w", pady=(theme.PAD_SMALL, 4))
        self._list = tk.Frame(self._body, bg=theme.Palette.panel)
        self._list.pack(fill="both", expand=True)

    # -- project wiring --------------------------------------------------------

    def set_project(self, project) -> None:
        self._project = project
        self.refresh_list()

    def refresh_list(self) -> None:
        """Re-derive the downloaded-tracks list from the project folder."""
        tracks = download.list_downloaded_tracks(self._project) if self._project else []
        for child in self._list.winfo_children():
            child.destroy()
        if not tracks:
            tk.Label(self._list, text="Nothing downloaded yet.", bg=theme.Palette.panel,
                     fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
                     justify="left", anchor="w", wraplength=self.width - theme.PAD * 2
                     ).pack(anchor="w", pady=theme.PAD_SMALL)
            return
        for name in tracks:
            row = tk.Frame(self._list, bg=theme.Palette.panel_light, cursor="hand2")
            row.pack(fill="x", pady=2)
            label = tk.Label(row, text=name, bg=theme.Palette.panel_light,
                             fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
                             anchor="w")
            label.pack(side="left", padx=theme.PAD_SMALL, pady=theme.PAD_SMALL)
            row.bind("<Button-1>", lambda _e, n=name: self._import_track(n))
            label.bind("<Button-1>", lambda _e, n=name: self._import_track(n))

    # -- download flow ------------------------------------------------------------

    def _on_download(self) -> None:
        if self._project is None or self._worker is not None:
            return
        url = self._entry_var.get().strip()
        if not url or url == _PLACEHOLDER:
            return
        self._set_running(True)
        self._worker = DownloadWorker(self._project, url)
        self._worker.start()
        self._poll()

    def _on_stop(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.stop()

    def _poll(self) -> None:
        worker = self._worker
        if worker is None:
            return
        while True:
            event = worker.poll()
            if event is None:
                break
            self._apply_event(event)
            if event.state in download.TERMINAL_STATES:
                self._worker = None
                break
        if self._worker is not None:
            self.after(_POLL_MS, self._poll)

    def _apply_event(self, event: download.DownloadEvent) -> None:
        if event.state == "done":
            self._set_idle()
            self._entry_var.set("")
            self._progress.configure(text=event.detail)
            self.refresh_list()
            self._sync_download_button()
        elif event.state in ("stopped", "error"):
            self._set_idle()
            self._progress.configure(text=event.detail)
            self._entry.focus_set()
        else:
            self._progress.configure(text=event.detail)

    # -- import -----------------------------------------------------------------

    def _import_track(self, filename: str) -> None:
        if self._on_import:
            self._on_import(filename)

    # -- state helpers -------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        state = "normal" if not running else "disabled"
        self._entry.configure(state=state)
        self._download_btn.configure(state=state)
        if running:
            self._stop_btn.pack(fill="x", pady=(0, theme.PAD_SMALL))
        else:
            self._stop_btn.pack_forget()

    def _set_idle(self) -> None:
        self._set_running(False)

    def _sync_download_button(self) -> None:
        text = self._entry_var.get().strip()
        if self._worker is not None:
            self._download_btn.configure(state="disabled")
        else:
            self._download_btn.configure(
                state="normal" if text and text != _PLACEHOLDER else "disabled"
            )

    def _on_focus_in(self, _event=None) -> None:
        if self._entry_var.get() == _PLACEHOLDER:
            self._entry.delete(0, "end")

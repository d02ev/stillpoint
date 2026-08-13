"""Editor screen: the window frame (spec 002).

Layout, top to bottom (contracts/editor-ui.md):

    +------------------------------------------------------------------+
    | First Mix                                        [Export]        |  top bar
    |------------------------------------------------------------------|
    | |I|  Background music                                            |
    | |I|  [Download from YouTube]  [Import from computer]             |  rail | panel
    | |I|                                                              |  host | main
    | |I|  Voice                             [▶/❚❚] (disabled)        |       | area
    | |I|  [Import from computer]                                      |
    +------------------------------------------------------------------+

This file is the only composer: it owns the top bar, the rail, the fixed-width
panel host, the two channel rows, and the transport, and wires their click
routes. Layout and visibility rules delegate to the small widgets and the pure
`PanelManager`. Every unimplemented interaction fails softly (FR-019).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog

from .. import dialogs, import_audio, model as model_mod, theme, youtube
from . import icons, panels
from .channels import MUSIC_ROLE, VOICE_ROLE, ChannelRow
from .download_panel import DownloadPanel
from .panels import AdjustmentPanel, ImagePanel, PanelManager
from .rail import Rail
from .transport import Transport
from .workers import ImportWorker

_PANEL_WIDTH = 260
_DEFAULT_GEOMETRY = "1280x760"
_MIN_SIZE = (960, 600)

_AUDIO_FILETYPES = [("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.opus"), ("All files", "*.*")]

_EXPORT_NOTICE = "Exporting isn't ready yet. You'll be able to save your video here soon."
_IMPORT_POLL_MS = 100


class EditorScreen(tk.Frame):
    def __init__(self, app, **kwargs):
        super().__init__(app.root, bg=theme.Palette.background, **kwargs)
        self.app = app
        self._panels = PanelManager()
        self._panel_widgets: dict[str, tk.Frame] = {}
        self._import_worker: ImportWorker | None = None
        self._import_role: str | None = None

        self._build_top_bar()
        self._build_body()
        self._build_panels()
        self._apply_panel_visibility()

    # -- construction ------------------------------------------------------

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self, bg=theme.Palette.panel, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._title_label = tk.Label(bar, text="", bg=theme.Palette.panel, fg=theme.Palette.text,
                                     font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold"))
        self._title_label.pack(side="left", padx=theme.PAD)

        self._export = tk.Button(
            bar, image=icons.get_icon("export", color=theme.Palette.text),
            text="Export", compound="left", command=self._on_export,
            bg=theme.Palette.accent, fg="#FFFFFF", activebackground=theme.Palette.accent_hover,
            activeforeground="#FFFFFF", relief="flat", highlightthickness=0,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"),
        )
        self._export.pack(side="right", padx=theme.PAD)

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=theme.Palette.background)
        body.pack(fill="both", expand=True)

        self._rail = Rail(body, on_toggle=self._on_rail_toggle)
        self._rail.pack(side="left", fill="y")

        self._panel_host = tk.Frame(body, bg=theme.Palette.panel, width=_PANEL_WIDTH)
        self._panel_host.pack_propagate(False)

        main = tk.Frame(body, bg=theme.Palette.background)
        main.pack(side="left", fill="both", expand=True, padx=theme.PAD)

        self._transport = Transport(main)
        self._transport.pack(side="bottom", anchor="w", pady=theme.PAD_SMALL)

        self._music_row = ChannelRow(
            main, role=MUSIC_ROLE,
            on_download=self._open_download_panel, on_import=lambda: self._start_import(MUSIC_ROLE),
            on_click=self._on_channel_click,
        )
        self._music_row.pack(fill="x", pady=theme.PAD_SMALL)

        self._voice_row = ChannelRow(
            main, role=VOICE_ROLE,
            on_download=None, on_import=lambda: self._start_import(VOICE_ROLE),
            on_click=self._on_channel_click,
        )
        self._voice_row.pack(fill="x", pady=theme.PAD_SMALL)

    def _build_panels(self) -> None:
        self._panel_widgets = {
            panels.PANEL_IMAGE: ImagePanel(self._panel_host),
            panels.PANEL_DOWNLOAD: DownloadPanel(self._panel_host, on_import=self._on_import_track),
            panels.PANEL_ADJUSTMENT: AdjustmentPanel(self._panel_host),
        }

    # -- project wiring ----------------------------------------------------

    def refresh(self) -> None:
        project = self.app.project
        if project is None:
            return
        self._title_label.configure(text=project.title)
        self._apply_window_geometry()

        music_state, music_name = _channel_state_for(project, MUSIC_ROLE)
        voice_state, voice_name = _channel_state_for(project, VOICE_ROLE)
        self._music_row.set_state(music_state, music_name)
        self._voice_row.set_state(voice_state, voice_name)

        self._panel_widgets[panels.PANEL_DOWNLOAD].set_project(project)

        self._panels.reset()
        self._apply_panel_visibility()

    def _apply_window_geometry(self) -> None:
        root = self.app.root
        try:
            root.geometry(_DEFAULT_GEOMETRY)
            root.minsize(*_MIN_SIZE)
        except tk.TclError:
            pass

    # -- panel visibility ----------------------------------------------------

    def _apply_panel_visibility(self) -> None:
        visible = self._panels.visible
        for panel_id, widget in self._panel_widgets.items():
            widget.pack_forget()
        if visible is not None:
            self._panel_host.pack(side="left", fill="y")
            widget = self._panel_widgets[visible]
            widget.pack(fill="both", expand=True)
            if visible == panels.PANEL_ADJUSTMENT:
                self._panel_widgets[visible].set_aim(self._panels.aim)
        else:
            self._panel_host.pack_forget()
        self._rail.set_active(visible)

    # -- click routes ---------------------------------------------------------

    def _on_rail_toggle(self, panel_id: str) -> None:
        self._panels.toggle(panel_id)
        self._apply_panel_visibility()

    def _open_download_panel(self) -> None:
        self._panels.open(panels.PANEL_DOWNLOAD)
        self._apply_panel_visibility()

    def _on_import_track(self, filename: str) -> None:
        """Import a downloaded track into the background-music channel.

        Performs the model write (immediate atomic save), then refreshes the
        channel row and the panel list in place — the panel stays open (FR-015).
        """
        project = self.app.project
        if project is None:
            return
        current = project.movie.audio
        if current is not None and current.filename == filename:
            return  # clicking the track already in the channel is a no-op (FR-017)
        try:
            project.set_background_music(filename)
        except ValueError:
            dialogs.info("Stillpoint", youtube.OTHER_MESSAGE, parent=self)
            return
        self._refresh_music_row()
        self._panel_widgets[panels.PANEL_DOWNLOAD].refresh_list()

    # -- local audio import ---------------------------------------------------

    def _start_import(self, role: str) -> None:
        """Run the pick → convert → assign flow for one channel (FR-003…FR-012).

        One import at a time: a second click while one is in flight shows the
        plain wait line and does nothing else (two conversions would contend on
        the weak CPU — Constitution II).
        """
        if self._import_worker is not None:
            dialogs.info("Stillpoint", import_audio.WAIT_MESSAGE, parent=self)
            return
        path = pick_audio_file(parent=self)
        if not path:
            return  # cancelling the picker changes nothing (FR-003)
        project = self.app.project
        if project is None:
            return
        self._import_role = role
        self._row_for(role).set_state("importing", import_audio.IMPORTING)
        worker = ImportWorker(project, path)
        self._import_worker = worker
        worker.start()
        self._poll_import()

    def _poll_import(self) -> None:
        worker = self._import_worker
        if worker is None:
            return
        while True:
            event = worker.poll()
            if event is None:
                break
            self._apply_import_event(event)
            if event.state in ("done", "error"):
                self._import_worker = None
                break
        if self._import_worker is not None:
            try:
                self.after(_IMPORT_POLL_MS, self._poll_import)
            except tk.TclError:
                pass

    def _apply_import_event(self, event) -> None:
        if event.state == "importing":
            self._row_for(self._import_role or MUSIC_ROLE).set_state("importing", event.detail)
        elif event.state == "done":
            self._finish_import(event.detail)
        elif event.state == "error":
            self._row_for(self._import_role or MUSIC_ROLE).set_state("empty", None)
            dialogs.info("Stillpoint", event.detail, parent=self)

    def _finish_import(self, filename: str) -> None:
        """Assign the stored copy to its channel's role and refresh the UI."""
        role = self._import_role or MUSIC_ROLE
        project = self.app.project
        if project is None:
            return
        try:
            if role == VOICE_ROLE:
                project.set_voice(filename)
            else:
                project.set_background_music(filename)
        except ValueError:
            self._row_for(role).set_state("empty", None)
            dialogs.info("Stillpoint", import_audio.OTHER_MESSAGE, parent=self)
            return
        state, name = _channel_state_for(project, role)
        self._row_for(role).set_state(state, name)
        self._panel_widgets[panels.PANEL_DOWNLOAD].refresh_list()

    def _row_for(self, role: str) -> ChannelRow:
        return self._music_row if role == MUSIC_ROLE else self._voice_row

    def _refresh_music_row(self) -> None:
        """Re-derive and repaint the music channel row without touching panels."""
        project = self.app.project
        if project is None:
            return
        state, name = _channel_state_for(project, MUSIC_ROLE)
        self._music_row.set_state(state, name)

    def _on_channel_click(self, role: str) -> None:
        self._panels.aim_at(role)
        if self._panels.visible != panels.PANEL_ADJUSTMENT:
            self._panels.open(panels.PANEL_ADJUSTMENT)
        self._apply_panel_visibility()

    def _on_export(self) -> None:
        dialogs.info("Stillpoint", _EXPORT_NOTICE, parent=self)


def _channel_state_for(project, role: str) -> tuple[str, str | None]:
    from .channels import channel_state

    return channel_state(project.movie, role)


def pick_audio_file(parent=None) -> str | None:
    """Native audio file picker, separated for monkeypatching in tests."""
    return filedialog.askopenfilename(parent=parent, title="Choose audio", filetypes=_AUDIO_FILETYPES)

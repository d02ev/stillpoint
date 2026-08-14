"""Editor screen: the window frame (spec 002, 005).

Layout, top to bottom (contracts/editor-ui.md):

    +------------------------------------------------------------------+
    | First Mix                                        [Export]        |  top bar
    |------------------------------------------------------------------|
    | |I|  Background music                                            |
    | |I|  [Download from YouTube]  [Import from computer]             |  rail | panel
    | |I|                                                              |  host | main
    | |I|  Voice                             [▶/❚❚] [Start over]      |       | area
    | |I|  [Import from computer]                                      |
    +------------------------------------------------------------------+

This file is the only composer: it owns the top bar, the rail, the fixed-width
panel host, the two channel rows, and the transport, and wires their click
routes. Layout and visibility rules delegate to the small widgets and the pure
`PanelManager`. Playback runs a real `PlaybackSession` (worker-thread bake,
streaming sink): play/pause/resume/Start over, end-of-mix returns the control
to play, and shaping-slider events are written through the project's atomic
save — their live re-bake is debounced and coalesced so a drag never stacks
ffmpeg bakes. Every still-unimplemented interaction fails softly (FR-019).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog

from .. import dialogs, import_audio, model as model_mod, theme, youtube
from .. import playback as playback_mod
from . import icons, panels, transport as transport_mod
from .channels import MUSIC_ROLE, VOICE_ROLE, ChannelRow
from .download_panel import DownloadPanel
from .panels import AdjustmentPanel, ImagePanel, PanelManager
from ..playback import PlaybackSession
from .rail import Rail
from .transport import Transport
from .workers import ImportWorker, PreviewWorker

_PANEL_WIDTH = 260
_DEFAULT_GEOMETRY = "1280x760"
_MIN_SIZE = (960, 600)

_AUDIO_FILETYPES = [("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.opus"), ("All files", "*.*")]

_EXPORT_NOTICE = "Exporting isn't ready yet. You'll be able to save your video here soon."
_IMPORT_POLL_MS = 100
_PREVIEW_POLL_MS = 100
_PLAYBACK_POLL_MS = 100
_REBAKE_DEBOUNCE_MS = 200


class EditorScreen(tk.Frame):
    def __init__(self, app, **kwargs):
        super().__init__(app.root, bg=theme.Palette.background, **kwargs)
        self.app = app
        self._panels = PanelManager()
        self._panel_widgets: dict[str, tk.Frame] = {}
        self._import_worker: ImportWorker | None = None
        self._import_role: str | None = None
        self._playback: PlaybackSession | None = None
        self._preview_worker: PreviewWorker | None = None
        self._playback_poll_id: str | None = None
        self._preview_poll_id: str | None = None
        self._rebake_id: str | None = None

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

        self._transport = Transport(main, on_play=self._on_transport, on_start_over=self._on_start_over)
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
            panels.PANEL_ADJUSTMENT: AdjustmentPanel(self._panel_host, on_setting=self._on_setting),
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
        self._panel_widgets[panels.PANEL_ADJUSTMENT].set_project(project)

        self._panels.reset()
        self._apply_panel_visibility()
        self._sync_transport()

    # -- playback ----------------------------------------------------------

    def _sync_transport(self) -> None:
        """Derive the transport's visual state from the project + session (FR-002).

        The control is available iff either channel is recorded; otherwise it
        is the plain unavailable state. When playback exists, its session state
        wins: PLAYING → pause, PAUSED → resume, BAKING → preparing.
        """
        project = self.app.project
        if project is None or not transport_mod.transport_available(project.movie):
            self._transport.set_state(transport_mod.UNAVAILABLE)
            return
        if self._playback is not None:
            state = self._playback.state
            if state == playback_mod.PLAYING:
                self._transport.set_state(transport_mod.PAUSE)
                return
            if state == playback_mod.PAUSED:
                self._transport.set_state(transport_mod.PLAY, paused=True)
                return
            if state == playback_mod.BAKING:
                self._transport.set_state(transport_mod.PREPARING)
                return
        self._transport.set_state(transport_mod.PLAY)

    def _on_transport(self) -> None:
        """The play/pause press: pause when playing, otherwise play/resume/bake."""
        self._cancel_rebake()
        if self._transport.state == transport_mod.PAUSE and self._playback is not None:
            self._playback.pause()
            self._cancel_playback_poll()
            self._sync_transport()
        else:
            self._start_playback()

    def _on_start_over(self) -> None:
        """The Start over press: discard any paused spot and play from the top.

        The session restarts its sink at byte 0 (Clarification Q3); the control
        shows pause again and the end-poll resumes (US-4 acceptance 2).
        """
        if self._playback is None:
            return
        self._cancel_rebake()
        try:
            self._playback.start_over()
        except Exception as exc:  # noqa: BLE001 - surfaced as a plain dialog
            self._playback_failed(exc)
            return
        self._sync_transport()
        self._schedule_playback_poll()

    def _start_playback(self) -> None:
        """Run a play press: resume/play directly, or bake then play.

        The bake runs on the PreviewWorker (never the UI thread) and is polled
        with ``root.after`` while BAKING; the control shows the honest
        `preparing` transient until the mix is baked and playing (FR-011).
        """
        if self._playback is None:
            self._playback = PlaybackSession()
        project = self.app.project
        if project is None:
            return
        try:
            action = self._playback.prepare_play(project)
        except Exception as exc:  # noqa: BLE001 - surfaced as a plain dialog
            self._playback_failed(exc)
            return
        if action == "bake":
            self._start_bake(project)
        else:
            self._sync_transport()
            self._schedule_playback_poll()

    def _start_bake(self, project) -> None:
        """Run the current bake on the PreviewWorker and poll it (never UI thread)."""
        self._transport.set_state(transport_mod.PREPARING)
        wav = self._playback.wav_path
        if wav is None:
            self._playback_failed(RuntimeError("no preview file"))
            return
        worker = PreviewWorker(project, wav)
        self._preview_worker = worker
        worker.start()
        self._poll_preview_worker()

    def _poll_preview_worker(self) -> None:
        worker = self._preview_worker
        if worker is None:
            self._preview_poll_id = None
            return
        while True:
            status = worker.poll()
            if status is None:
                break
            self._apply_preview_status(status)
            if status.state in ("done", "error"):
                self._preview_worker = None
                break
        if self._preview_worker is not None:
            try:
                self._preview_poll_id = self.after(_PREVIEW_POLL_MS, self._poll_preview_worker)
            except tk.TclError:
                self._preview_poll_id = None

    def _apply_preview_status(self, status) -> None:
        if status.state == "done":
            try:
                self._playback.bake_done(status.value)
            except Exception as exc:  # noqa: BLE001 - a failed open/play
                self._playback_failed(exc)
                return
            self._sync_transport()
            project = self.app.project
            if project is not None and self._playback.needs_rebake(project):
                # A setting changed while this bake ran; apply it once the
                # knob settles so bakes never overlap and the change is never
                # lost (FR-015, SC-003, Constitution II).
                self._schedule_rebake()
                return
            self._schedule_playback_poll()
        elif status.state == "error":
            self._playback_failed(status.value)

    def _cancel_playback_poll(self) -> None:
        if self._playback_poll_id is not None:
            try:
                self.after_cancel(self._playback_poll_id)
            except tk.TclError:
                pass
            self._playback_poll_id = None

    def _schedule_playback_poll(self) -> None:
        """Poll the sink for the mix end — only while playing (Constitution II).

        No timer is scheduled while paused, so idle CPU stays at effectively
        zero; the mix's end turns the control back to play (US-4 acceptance 1).
        """
        self._cancel_playback_poll()
        try:
            self._playback_poll_id = self.after(_PLAYBACK_POLL_MS, self._poll_playback)
        except tk.TclError:
            self._playback_poll_id = None

    def _poll_playback(self) -> None:
        self._playback_poll_id = None
        if self._playback is None or self._playback.state != playback_mod.PLAYING:
            return  # paused: nothing scheduled, nothing polled (Constitution II)
        try:
            finished = self._playback.check_finished()
        except Exception as exc:  # noqa: BLE001 - a mid-play failure
            self._playback_failed(exc)
            return
        if finished:
            self._sync_transport()
            return
        self._schedule_playback_poll()

    def _playback_failed(self, exc) -> None:
        """A bake/open/play failure: plain dialog, control back to play (FR-011)."""
        if self._playback is not None:
            if self._playback.state == playback_mod.BAKING:
                self._playback.bake_failed()
            else:
                self._playback.stop()
        _, message = playback_mod.classify_playback_error(exc)
        try:
            dialogs.info("Stillpoint", message, parent=self)
        except tk.TclError:
            pass
        self._sync_transport()

    def stop_playback(self) -> None:
        """Stop playback and clean up on leave/close (FR-012).

        Cancels the pending polls, stops the session (closing the audio device
        and deleting the temp WAV), and resets the transport. The project is
        untouched.
        """
        self._cancel_playback_poll()
        self._cancel_rebake()
        if self._preview_poll_id is not None:
            try:
                self.after_cancel(self._preview_poll_id)
            except tk.TclError:
                pass
            self._preview_poll_id = None
        self._preview_worker = None
        if self._playback is not None:
            try:
                self._playback.stop()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            self._playback = None
        try:
            self._transport.set_state(transport_mod.UNAVAILABLE)
        except (tk.TclError, RuntimeError):
            pass

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

    def _on_setting(self, role: str, setting: str, value: float) -> None:
        """A shaping change: persist the edit-state, apply it live, gently.

        The composer owns model writes (the 003/004 separation of concerns).
        The persisted write is immediate and atomic on every tick (the R2
        invariant); the live re-bake is debounced and coalesced so a drag
        never starts one ffmpeg bake per tick — the old mix keeps playing
        until the new one is ready, then playback continues from the current
        spot with the new shaping (FR-015, SC-003, Constitution II).
        """
        project = self.app.project
        if project is None:
            return
        try:
            project.set_channel_setting(role, setting, value)
        except ValueError:
            pass  # aimed channel vanished mid-drag — a no-op, never a crash
        if self._playback is None:
            return
        self._schedule_rebake()

    def _schedule_rebake(self) -> None:
        """Debounce + coalesce a live re-bake after any shaping change.

        The bake starts ``_REBAKE_DEBOUNCE_MS`` after the last change, so a
        drag settles to its final value before the whole-mix re-bake runs and
        the old mix keeps playing meanwhile. ``_run_rebake`` refuses to
        start a second bake while one is in flight (two ffmpeg bakes never
        contend, Constitution II); a change made during a bake is then seen by
        ``needs_rebake`` when it completes and re-baked once the knob settles.
        """
        self._cancel_rebake()
        if self._playback is None or self._playback.state not in (
            playback_mod.PLAYING, playback_mod.PAUSED, playback_mod.FINISHED,
        ):
            return
        try:
            self._rebake_id = self.after(_REBAKE_DEBOUNCE_MS, self._run_rebake)
        except tk.TclError:
            self._rebake_id = None

    def _run_rebake(self) -> None:
        """The settled shaping change: re-bake and keep her spot if needed."""
        self._rebake_id = None
        project = self.app.project
        if self._playback is None or project is None:
            return
        if self._preview_worker is not None:
            return  # a bake is in flight — it will see the change itself
        try:
            action = self._playback.settings_changed(project)
        except Exception as exc:  # noqa: BLE001 - surfaced as a plain dialog
            self._playback_failed(exc)
            return
        if action == "bake":
            self._start_bake(project)

    def _cancel_rebake(self) -> None:
        if self._rebake_id is not None:
            try:
                self.after_cancel(self._rebake_id)
            except tk.TclError:
                pass
            self._rebake_id = None

    def _on_export(self) -> None:
        dialogs.info("Stillpoint", _EXPORT_NOTICE, parent=self)


def _channel_state_for(project, role: str) -> tuple[str, str | None]:
    from .channels import channel_state

    return channel_state(project.movie, role)


def pick_audio_file(parent=None) -> str | None:
    """Native audio file picker, separated for monkeypatching in tests."""
    return filedialog.askopenfilename(parent=parent, title="Choose audio", filetypes=_AUDIO_FILETYPES)

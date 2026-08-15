"""The three docked panels (image, audio-download, audio-adjustment).

``PanelManager`` is a pure, display-free class owning the one-panel-at-a-time
and aim rules (FR-007, FR-008, FR-015) so they are unit-testable headlessly.
The real widgets live in their own modules — ``gui/image_panel.py`` (Spec 7),
``gui/download_panel.py`` (Spec 3) — and ``ImagePanel`` is re-exported here
lazily so importing ``panels`` never pulls Tk-only widgets. The adjustment
panel is the real per-channel sound widget (Spec 5).
"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from ..model import FADE_MAX_SECONDS

PANEL_IMAGE = "image"
PANEL_DOWNLOAD = "download"
PANEL_ADJUSTMENT = "adjustment"

PANEL_TITLES = {
    PANEL_IMAGE: "Pictures",
    PANEL_DOWNLOAD: "Download music",
    PANEL_ADJUSTMENT: "Adjust sound",
}

# Canonical everyday-language strings (Constitution I, shaping-panel-ui.md).
EMPTY_AIM_NOTE = "Add music or your voice first to shape its sound."
OFF_LABEL = "Off"
VOLUME_LABEL = "Volume"
ECHO_LABEL = "Echo"
FADE_IN_LABEL = "Fade in"
FADE_OUT_LABEL = "Fade out"

#: Maximum echo strength (0..1) — the slider's top stop (research Decision 4).
ECHO_MAX = 1.0


class PanelManager:
    """Visibility rules for the three docked panels (no Tk dependency)."""

    def __init__(self) -> None:
        self.visible: str | None = None
        self.aim: str = "music"

    def open(self, panel: str) -> None:
        """Show one panel; any previously visible panel is closed (FR-008)."""
        if panel not in PANEL_TITLES:
            raise ValueError(f"unknown panel: {panel!r}")
        self.visible = panel

    def toggle(self, panel: str) -> None:
        """If the panel is open, close it; otherwise open it (FR-007)."""
        if self.visible == panel:
            self.visible = None
        else:
            self.open(panel)

    def aim_at(self, role: str) -> None:
        """Point the adjustment panel at a channel. Never closes a panel (FR-015)."""
        self.aim = role

    def reset(self) -> None:
        """Close every panel and reset the aim (FR-006)."""
        self.visible = None
        self.aim = "music"


class _PanelFrame(tk.Frame):
    """Shared chrome for a docked panel: title bar + body area."""

    width = 260

    def __init__(self, master, title: str, **kwargs):
        super().__init__(master, bg=theme.Palette.panel, width=self.width, **kwargs)
        self.pack_propagate(False)
        tk.Label(self, text=title, bg=theme.Palette.panel, fg=theme.Palette.text,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold")
                 ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD_SMALL, 4))
        self._body = tk.Frame(self, bg=theme.Palette.panel)
        self._body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD_SMALL))

    def _note(self, text: str) -> None:
        tk.Label(self._body, text=text, bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE), justify="left", wraplength=self.width - theme.PAD * 2
                 ).pack(anchor="w", pady=theme.PAD_SMALL)


class AdjustmentPanel(_PanelFrame):
    """The per-channel sound controls (006): four labeled sliders — Volume,
    Echo, Fade in, Fade out — for the aimed loaded channel, each minimum stop
    labeled "Off" and each slider's position shown as a live percentage
    readout (e.g. "60%"). Empty aim shows the plain "Add music or your voice
    first to shape its sound." line instead of any control (FR-003). The
    editor owns model writes — this widget only reports ``on_setting(role,
    setting, value)`` and never touches the project itself.
    """

    def __init__(self, master, on_setting=None, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_ADJUSTMENT], **kwargs)
        self._on_setting = on_setting
        self._project = None
        self._aim = "music"
        self._scales: dict[str, tk.Scale] = {}
        self._percent_labels: dict[str, tk.Label] = {}
        self._aim_label = tk.Label(
            self._body, text="", bg=theme.Palette.panel, fg=theme.Palette.accent,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"))
        self._aim_label.pack(anchor="w", pady=(0, theme.PAD_SMALL))
        self._sound_body = tk.Frame(self._body, bg=theme.Palette.panel)
        self._sound_body.pack(fill="both", expand=True)
        self._set_sound_section()

    @property
    def _scale(self) -> tk.Scale | None:
        """Compatibility alias: the Volume slider (delivered tests)."""
        return self._scales.get("volume")

    def set_project(self, project) -> None:
        self._project = project
        self._set_sound_section()

    def set_aim(self, role: str) -> None:
        from .channels import CHANNEL_TITLES

        self._aim = role
        self._aim_label.configure(text=CHANNEL_TITLES.get(role, role))
        self._set_sound_section()

    def _channel_item(self):
        if self._project is None:
            return None
        if self._aim == "music":
            return self._project.movie.audio
        if self._aim == "voice":
            return self._project.movie.voice
        return None

    def _set_sound_section(self) -> None:
        """Rebuild the aimed channel's controls — four sliders or the empty
        line, never both and never two channels (FR-003, FR-004)."""
        for child in self._sound_body.winfo_children():
            child.destroy()
        self._scales = {}
        self._percent_labels = {}
        item = self._channel_item()
        if item is None:
            tk.Label(self._sound_body, text=EMPTY_AIM_NOTE, bg=theme.Palette.panel,
                     fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
                     justify="left", wraplength=self.width - theme.PAD * 2
                     ).pack(anchor="w", pady=theme.PAD_SMALL)
            return
        self._add_setting_row("volume", VOLUME_LABEL, item.volume)
        self._add_setting_row("echo", ECHO_LABEL, item.echo)
        self._add_setting_row("fade_in", FADE_IN_LABEL, item.fade_in)
        self._add_setting_row("fade_out", FADE_OUT_LABEL, item.fade_out)

    def _add_setting_row(self, setting: str, label_text: str, value: float) -> None:
        """One labeled slider whose leftmost stop reads "Off" (FR-009) and
        whose live position reads as a percentage on the right."""
        frame = tk.Frame(self._sound_body, bg=theme.Palette.panel)
        frame.pack(fill="x", pady=(theme.PAD_SMALL, 0))
        tk.Label(frame, text=label_text, bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE)
                 ).pack(anchor="w")
        scale_row = tk.Frame(frame, bg=theme.Palette.panel)
        scale_row.pack(fill="x")
        tk.Label(scale_row, text=OFF_LABEL, bg=theme.Palette.panel,
                 fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE)
                 ).pack(side="left", padx=(0, 2))
        position = self._slider_position(setting, value)
        percent = tk.Label(scale_row, text=self._percent_text(position),
                           bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                           font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        percent.pack(side="right", padx=(2, 0))
        scale = tk.Scale(
            scale_row, from_=0, to=100, orient="horizontal",
            showvalue=False, bg=theme.Palette.panel, fg=theme.Palette.text,
            highlightthickness=0, troughcolor=theme.Palette.panel_light,
            activebackground=theme.Palette.accent,
        )
        scale.set(position)
        scale.configure(command=lambda v, s=setting: self._on_slider(s, v))
        scale.pack(side="left", fill="x", expand=True)
        self._scales[setting] = scale
        self._percent_labels[setting] = percent

    @staticmethod
    def _slider_position(setting: str, value: float) -> int:
        """Map a stored value to the 0..100 slider position."""
        if setting in ("volume", "echo"):
            return int(round(value * 100))
        return int(round(value / FADE_MAX_SECONDS * 100))

    @staticmethod
    def _slider_value(setting: str, value: str) -> float:
        """Map a slider position string to the persisted value — 0..1 for
        volume/echo, seconds for fades."""
        position = float(value)
        if setting in ("volume", "echo"):
            return position / 100.0
        return position / 100.0 * FADE_MAX_SECONDS

    @staticmethod
    def _percent_text(position: int) -> str:
        """The human-readable readout for a slider position — "60%" (FR-009)."""
        return f"{int(position)}%"

    def _on_slider(self, setting: str, value: str) -> None:
        """A slider move: report on_setting(role, setting, value) — report-only."""
        percent = self._percent_labels.get(setting)
        if percent is not None:
            percent.configure(text=self._percent_text(value))
        if self._on_setting is not None:
            self._on_setting(self._aim, setting, self._slider_value(setting, value))

    def _on_scale(self, value: str) -> None:
        """Compatibility alias: a Volume move reports ``volume`` (delivered tests)."""
        self._on_slider("volume", value)


def __getattr__(name: str):
    """Lazy re-export of the real image panel (module attribute access only)."""
    if name == "ImagePanel":
        from .image_panel import ImagePanel

        return ImagePanel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

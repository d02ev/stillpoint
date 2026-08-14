"""The three docked panels (image, audio-download, audio-adjustment).

``PanelManager`` is a pure, display-free class owning the one-panel-at-a-time
and aim rules (FR-007, FR-008, FR-015) so they are unit-testable headlessly.
The image panel is a thin stub frame (real logic is Spec 7); the adjustment
panel is the real per-channel sound widget (Spec 5); the audio-download panel
is the real widget in ``gui/download_panel.py`` (Spec 3).
"""

from __future__ import annotations

import tkinter as tk

from .. import theme

PANEL_IMAGE = "image"
PANEL_DOWNLOAD = "download"
PANEL_ADJUSTMENT = "adjustment"

PANEL_TITLES = {
    PANEL_IMAGE: "Pictures",
    PANEL_DOWNLOAD: "Download music",
    PANEL_ADJUSTMENT: "Adjust sound",
}

# Canonical everyday-language string (Constitution I, preview-playback-ui.md).
EMPTY_AIM_NOTE = "Add music or your voice first to adjust its sound."
VOLUME_LABEL = "Volume"


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


class ImagePanel(_PanelFrame):
    """Stub: picture search/add arrives in Spec 7."""

    def __init__(self, master, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_IMAGE], **kwargs)
        self._note("You'll be able to find and add pictures for your film here.")


class AdjustmentPanel(_PanelFrame):
    """The per-channel sound controls (Spec 5): one Volume slider per loaded
    channel, aimed at the clicked channel. Empty aim shows the plain
    "Add music or your voice first…" line instead of a slider (FR-014,
    Constitution VI). The editor owns model writes — this widget only reports
    ``on_volume(role, value_in_0_1)`` and never touches the project itself.
    """

    def __init__(self, master, on_volume=None, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_ADJUSTMENT], **kwargs)
        self._on_volume = on_volume
        self._project = None
        self._aim = "music"
        self._scale: tk.Scale | None = None
        self._aim_label = tk.Label(
            self._body, text="", bg=theme.Palette.panel, fg=theme.Palette.accent,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"))
        self._aim_label.pack(anchor="w", pady=(0, theme.PAD_SMALL))
        self._sound_body = tk.Frame(self._body, bg=theme.Palette.panel)
        self._sound_body.pack(fill="both", expand=True)
        self._set_sound_section()

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
        """Rebuild the aimed channel's controls (never two sliders, FR-014)."""
        for child in self._sound_body.winfo_children():
            child.destroy()
        self._scale = None
        item = self._channel_item()
        if item is None:
            tk.Label(self._sound_body, text=EMPTY_AIM_NOTE, bg=theme.Palette.panel,
                     fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
                     justify="left", wraplength=self.width - theme.PAD * 2
                     ).pack(anchor="w", pady=theme.PAD_SMALL)
            return
        tk.Label(self._sound_body, text=VOLUME_LABEL, bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE)
                 ).pack(anchor="w", pady=(theme.PAD_SMALL, 0))
        scale = tk.Scale(
            self._sound_body, from_=0, to=100, orient="horizontal",
            showvalue=False, bg=theme.Palette.panel, fg=theme.Palette.text,
            highlightthickness=0, troughcolor=theme.Palette.panel_light,
            activebackground=theme.Palette.accent,
        )
        scale.set(int(round(item.volume * 100)))
        scale.configure(command=self._on_scale)
        scale.pack(fill="x", pady=theme.PAD_SMALL)
        self._scale = scale

    def _on_scale(self, value: str) -> None:
        if self._on_volume is not None:
            self._on_volume(self._aim, float(value) / 100.0)

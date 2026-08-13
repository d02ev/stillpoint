"""The three docked panels (image, audio-download, audio-adjustment).

``PanelManager`` is a pure, display-free class owning the one-panel-at-a-time
and aim rules (FR-007, FR-008, FR-015) so they are unit-testable headlessly.
The three panel widgets below are thin frames packed into the fixed-width
column beside the rail; their contents are stubs (real logic is Specs 3, 4-6, 7).
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


class DownloadPanel(_PanelFrame):
    """Stub: fetching music from YouTube arrives in Spec 3."""

    def __init__(self, master, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_DOWNLOAD], **kwargs)
        self._note("You'll be able to fetch music from YouTube here.")


class AdjustmentPanel(_PanelFrame):
    """Stub: real controls arrive in Specs 4-6. Shows which channel it aims at."""

    def __init__(self, master, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_ADJUSTMENT], **kwargs)
        self._aim_label = tk.Label(
            self._body, text="", bg=theme.Palette.panel, fg=theme.Palette.accent,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"))
        self._aim_label.pack(anchor="w", pady=(0, theme.PAD_SMALL))
        self._note("Balancing and shaping controls will live here.")

    def set_aim(self, role: str) -> None:
        from .channels import CHANNEL_TITLES

        self._aim_label.configure(text=CHANNEL_TITLES.get(role, role))

"""The play/pause preview transport (FR-017).

In this slice it is ALWAYS visibly unavailable (disabled appearance) with a
plain-language tooltip; enablement and behavior are Spec 5. Clicking it does
nothing.
"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from . import icons
from .tooltip import bind_tooltip

TOOLTIP = "Preview needs audio — coming soon."


class Transport(tk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, bg=theme.Palette.background, **kwargs)
        self._button = tk.Button(
            self, image=icons.get_icon("play", disabled=True),
            text="Play", compound="left", state="disabled",
            bg=theme.Palette.panel_light, fg=theme.Palette.disabled_text,
            activebackground=theme.Palette.panel_light, activeforeground=theme.Palette.disabled_text,
            disabledforeground=theme.Palette.disabled_text, relief="flat",
            highlightthickness=1, highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        )
        self._button.pack(anchor="w")
        bind_tooltip(self._button, TOOLTIP)

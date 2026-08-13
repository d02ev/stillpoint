"""The thin three-icon rail on the editor's left edge (FR-005).

Each icon is icon-only with a plain-language hover tooltip (FR-004). Clicking
delegates to a callback; the one-panel-at-a-time rules live in `PanelManager`.
"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from . import icons, panels
from .tooltip import bind_tooltip

# (icon name, tooltip text, panel id) — exactly three, in this order.
_RAIL_ITEMS = (
    ("picture", "Picture", panels.PANEL_IMAGE),
    ("download", "Download music", panels.PANEL_DOWNLOAD),
    ("adjust", "Adjust sound", panels.PANEL_ADJUSTMENT),
)


class Rail(tk.Frame):
    def __init__(self, master, on_toggle, **kwargs):
        super().__init__(master, bg=theme.Palette.rail, width=56, **kwargs)
        self.pack_propagate(False)
        self._buttons: list[tk.Button] = []
        for icon_name, tooltip, panel_id in _RAIL_ITEMS:
            btn = tk.Button(
                self, image=icons.get_icon(icon_name, color=theme.Palette.text),
                command=lambda p=panel_id: on_toggle(p),
                bg=theme.Palette.rail, fg=theme.Palette.text,
                activebackground=theme.Palette.accent_soft, activeforeground=theme.Palette.text,
                relief="flat", highlightthickness=0,
                padx=theme.PAD_SMALL, pady=theme.PAD_SMALL,
            )
            btn.pack(side="top", fill="x", padx=theme.PAD_SMALL, pady=theme.PAD_SMALL)
            bind_tooltip(btn, tooltip)
            self._buttons.append(btn)

    def set_active(self, panel_id: str | None) -> None:
        """Highlight the button whose panel is open (or none)."""
        for btn, (_icon, _tip, item_panel) in zip(self._buttons, _RAIL_ITEMS):
            btn.configure(bg=theme.Palette.accent_soft if item_panel == panel_id else theme.Palette.rail)

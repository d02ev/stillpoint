"""Theme constants for the UI.

Palette, fonts and spacing live here so the look stays consistent and can be
changed in one place.
"""

from __future__ import annotations

# -- colours --------------------------------------------------------------

class Palette:
    """Named colours; every accent is derived from the single 'accent'."""

    accent = "#8E7CC3"
    accent_hover = "#A08ED0"
    accent_soft = "#3B3450"

    background = "#12121A"
    panel = "#1A1A24"
    panel_light = "#20202C"
    border = "#2E2E3E"

    text = "#ECECF2"
    text_dim = "#9A9AA8"
    text_faint = "#6E6E7E"

    danger = "#C25A5A"
    danger_hover = "#D07070"

    good = "#6DBE7B"

    # Alpha-free overlay used by the picture-in-picture preview.
    preview_overlay = "#000000"


# -- typography -------------------------------------------------------------

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 10
FONT_SIZE_TITLE = 16
FONT_SIZE_BIG = 26


# -- spacing ---------------------------------------------------------------

PAD = 12
PAD_SMALL = 6
PAD_LARGE = 24
RADIUS = 8
BUTTON_HEIGHT = 34

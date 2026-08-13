"""Theme constants for the UI.

Palette, fonts and spacing live here so the look stays consistent and can be
changed in one place.
"""

from __future__ import annotations

# -- colours --------------------------------------------------------------

class Palette:
    """Named colours; every accent is derived from the single 'accent'.

    The editor frame is light and warm throughout (spec 002 FR-001, SC-007):
    no dark surfaces, all text readable against its background.
    """

    accent = "#B66B2E"  # warm clay orange; the one accent all others derive from
    accent_hover = "#C97E3F"
    accent_soft = "#EFD9BE"  # pale warm tint for selected rows

    background = "#F7F1E7"  # warm paper
    panel = "#F1E7D6"  # slightly deeper warm surface
    panel_light = "#FBF7F0"  # raised surfaces, buttons

    border = "#E0D2BA"
    rail = "#EDE2CE"  # the thin icon rail column

    text = "#3A3228"  # warm near-black, strong contrast on every surface
    text_dim = "#6E6353"
    text_faint = "#9C917F"

    disabled = "#C9BCA6"  # disabled control / icon tint
    disabled_text = "#B4A68E"

    danger = "#B14A38"
    danger_hover = "#C25A46"

    good = "#5E8B53"

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

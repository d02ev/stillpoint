"""Small theme-colored icons generated once with Pillow.

Every editor glyph (picture, download, sliders, import, play/pause, export) is
drawn as a tiny RGBA image at first use and cached as a Tk PhotoImage for the
app lifetime, so idle CPU stays at zero (Constitution II). Colors come from the
theme palette so the icons inherit the warm look, and dim/disabled variants use
the palette's disabled tint.

The pure drawing lives in :func:`render_icon` (no Tk, unit-testable headlessly);
:func:`get_icon` wraps it in a cached PhotoImage for widgets.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageTk

from .. import theme

_ICON_SIZE = 18
_CACHE: dict[tuple[str, int, str], ImageTk.PhotoImage] = {}


def _line(draw: ImageDraw.ImageDraw, size: int, p1, p2, color, width: int = 2) -> None:
    draw.line((p1, p2), fill=color, width=max(1, width))


def render_icon(name: str, size: int = _ICON_SIZE, color: str = theme.Palette.text, disabled: bool = False) -> Image.Image:
    """Draw one glyph onto a transparent RGBA image (no Tk dependency)."""
    use_color = theme.Palette.disabled if disabled else color
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = float(size)
    m = max(2, size // 6)  # margin
    c = m + s * 0.18  # inner left/right bounds
    d2 = s - m - s * 0.18  # inner right/bottom bounds
    midx = size / 2

    if name == "picture":
        # A rounded picture frame with a sun and a mountain.
        d.rounded_rectangle((m, m, size - m, size - m), radius=2, outline=use_color, width=2)
        d.ellipse((c + s * 0.08, m + s * 0.12, c + s * 0.28, m + s * 0.32), fill=use_color)
        _line(d, size, (m + s * 0.1, size - m - s * 0.1), (c + s * 0.25, size - m - s * 0.35), use_color)
        _line(d, size, (c + s * 0.25, size - m - s * 0.35), (midx + s * 0.05, size - m - s * 0.05), use_color)
    elif name == "download":
        # Down arrow into a tray: "get music".
        _line(d, size, (midx, m + 1), (midx, size - m - 3), use_color, width=2)
        d.polygon([(midx - s * 0.16, size - m - s * 0.22), (midx + s * 0.16, size - m - s * 0.22), (midx, size - m - 2)], fill=use_color)
        _line(d, size, (m + 1, size - m - 1), (size - m, size - m - 1), use_color, width=2)
    elif name == "adjust":
        # Three horizontal sliders with knobs.
        for i, (y, kx) in enumerate(((s * 0.28, s * 0.62), (s * 0.5, s * 0.3), (s * 0.72, s * 0.55))):
            _line(d, size, (m, y), (size - m, y), use_color, width=2)
            r = max(2, size // 8)
            d.ellipse((kx * s - r, y - r, kx * s + r, y + r), fill=theme.Palette.background if not disabled else use_color,
                      outline=use_color, width=2)
    elif name == "import":
        # A box with an arrow heading in from the left: "bring in a file".
        d.rounded_rectangle((m, s * 0.4, size - m, size - m), radius=2, outline=use_color, width=2)
        _line(d, size, (m + 2, s * 0.22), (size * 0.42, s * 0.22), use_color, width=2)
        d.polygon([(size * 0.3, s * 0.08), (size * 0.3, s * 0.38), (size * 0.1, s * 0.22)], outline=use_color, fill=use_color)
    elif name == "play":
        d.polygon([(m + 2, m), (size - m - 1, midx), (m + 2, size - m)], fill=use_color)
    elif name == "pause":
        w = max(2, size // 6)
        x0 = size * 0.32
        d.rounded_rectangle((x0 - w / 2, m, x0 + w / 2, size - m), radius=1, fill=use_color)
        x1 = size * 0.68
        d.rounded_rectangle((x1 - w / 2, m, x1 + w / 2, size - m), radius=1, fill=use_color)
    elif name == "export":
        # An up arrow out of a tray: "take your film out".
        _line(d, size, (midx, size - m - 3), (midx, m + 3), use_color, width=2)
        d.polygon([(midx - s * 0.16, m + s * 0.22), (midx + s * 0.16, m + s * 0.22), (midx, m + 2)], fill=use_color)
        _line(d, size, (m + 1, size - m - 1), (size - m, size - m - 1), use_color, width=2)
    else:
        raise ValueError(f"unknown icon name: {name!r}")

    return img


def get_icon(name: str, size: int = _ICON_SIZE, color: str = theme.Palette.text, disabled: bool = False) -> ImageTk.PhotoImage:
    """Return a cached PhotoImage for a glyph (calls into Tk)."""
    key = (name, size, color, disabled)
    if key not in _CACHE:
        img = render_icon(name, size=size, color=color, disabled=disabled)
        _CACHE[key] = ImageTk.PhotoImage(img)
    return _CACHE[key]

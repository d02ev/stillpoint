"""Waveform widget: a thin canvas that draws amplitude peaks as bars."""

from __future__ import annotations

import tkinter as tk

from .. import theme


class Waveform(tk.Canvas):
    """Draws a list of peak values (0..1) as vertical bars."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("height", 40)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bg", theme.Palette.panel)
        super().__init__(master, **kwargs)
        self._peaks: list[float] = []
        self._color = theme.Palette.accent

    def set_peaks(self, peaks: list[float]) -> None:
        self._peaks = list(peaks)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width() or 200
        height = self.winfo_height() or 40
        if not self._peaks:
            self.create_text(width // 2, height // 2, text="no audio",
                             fill=theme.Palette.text_faint,
                             font=(theme.FONT_FAMILY, theme.FONT_SIZE))
            return
        bar_w = width / len(self._peaks)
        for index, peak in enumerate(self._peaks):
            bar_h = max(2, int(peak * (height - 6)))
            x0 = index * bar_w
            y0 = height // 2 - bar_h // 2
            self.create_rectangle(x0, y0, x0 + bar_w - 1, y0 + bar_h,
                                  fill=self._color, outline="")

    def on_resize(self, _event=None) -> None:
        self.redraw()

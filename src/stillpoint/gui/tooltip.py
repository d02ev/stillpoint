"""A tiny hover-tooltip helper (research Decision 3).

Shows a plain label near the pointer ~400 ms after the mouse enters a widget and
hides it on leave. Uses a single one-shot `after` timer, so there is no polling
and idle CPU stays at zero (Constitution II).
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .. import theme

_DELAY_MS = 400


def bind_tooltip(widget: tk.Widget, text: str | Callable[[], str]) -> None:
    """Attach a hover tooltip to a widget. Safe to call more than once.

    ``text`` may be a plain string or a zero-arg callable resolved each time the
    tooltip is shown, so a control's tooltip can track its live state (e.g. the
    transport always says what it will do next, FR-001).
    """
    timer_id: str | None = None
    tip: tk.Toplevel | None = None

    def _resolve() -> str:
        return text() if callable(text) else text

    def _show(_event) -> None:
        nonlocal timer_id
        if timer_id is not None:
            widget.after_cancel(timer_id)
        timer_id = widget.after(_DELAY_MS, lambda: _present(widget.winfo_pointerx(), widget.winfo_pointery()))

    def _present(x: int, y: int) -> None:
        nonlocal tip
        _hide(None)
        tip = tk.Toplevel(widget)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.configure(bg=theme.Palette.border)
        tk.Label(
            tip, text=_resolve(), bg=theme.Palette.background, fg=theme.Palette.text,
            padx=6, pady=3, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        ).pack()
        tip.update_idletasks()
        tip.geometry(f"+{x + 12}+{y + 14}")
        tip.deiconify()

    def _hide(_event) -> None:
        nonlocal timer_id, tip
        if timer_id is not None:
            widget.after_cancel(timer_id)
            timer_id = None
        if tip is not None:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", _show, add="+")
    widget.bind("<Leave>", _hide, add="+")

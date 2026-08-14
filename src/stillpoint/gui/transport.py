"""The play/pause/start-over preview transport (Spec 5, contracts/preview-playback-ui.md).

A single control whose glyph and tooltip always say what it will do next
(FR-001): disabled ▶ with a plain reason when nothing is recorded, enabled ▶ to
play (or resume), enabled ❚❚ to pause, and a disabled ▶ while the mix bakes —
an honest, visible transient that never leaves the control stuck (FR-011).
A small **Start over** text button appears once playback has begun and returns
her to the top (Clarification Q3). Play/pause/restart only: no position bar and
no seek anywhere (FR-004).

The enablement rule is the pure :func:`transport_available` (display-free,
unit-tested headlessly per research Decision 6); the widget itself is the thin
renderer over the four states.
"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from . import icons
from .tooltip import bind_tooltip

# Canonical everyday-language strings (Constitution I).
UNAVAILABLE_TOOLTIP = "Add music or your voice to preview"
PREPARING_TOOLTIP = "Preparing preview…"
PLAY_FROM_TOP_TOOLTIP = "Play the preview from the top"
RESUME_TOOLTIP = "Resume the preview"
PAUSE_TOOLTIP = "Pause the preview"
START_OVER_TOOLTIP = "Start the preview again from the top"

# The four visual states (contracts/preview-playback-ui.md).
UNAVAILABLE = "unavailable"
PLAY = "play"
PAUSE = "pause"
PREPARING = "preparing"

_STATES = (UNAVAILABLE, PLAY, PAUSE, PREPARING)


def transport_available(movie) -> bool:
    """Pure: a preview is possible iff either channel is recorded (FR-002).

    "Recorded" means present in the project model — never a disk check, so a
    channel whose file is missing still enables the control (its missing file
    is baked into silence, FR-010).
    """
    return movie.audio is not None or movie.voice is not None


class Transport(tk.Frame):
    def __init__(self, master, on_play=None, on_start_over=None, **kwargs):
        super().__init__(master, bg=theme.Palette.background, **kwargs)
        self._on_play = on_play
        self._on_start_over_cb = on_start_over
        self._state = UNAVAILABLE
        self._paused = False
        self._icon = "play"
        self._enabled = False

        self._button = tk.Button(
            self, image=icons.get_icon("play", disabled=True),
            text="Play", compound="left", state="disabled",
            command=self._on_play,
            bg=theme.Palette.panel_light, fg=theme.Palette.disabled_text,
            activebackground=theme.Palette.panel_light, activeforeground=theme.Palette.disabled_text,
            disabledforeground=theme.Palette.disabled_text, relief="flat",
            highlightthickness=1, highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        )
        self._button.pack(anchor="w")
        bind_tooltip(self._button, self.tooltip)

        self._start_over = tk.Button(
            self, text="Start over", state="disabled",
            command=self._on_start_over,
            bg=theme.Palette.background, fg=theme.Palette.disabled_text,
            activebackground=theme.Palette.background, activeforeground=theme.Palette.disabled_text,
            disabledforeground=theme.Palette.disabled_text, relief="flat",
            padx=0, pady=0, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        )
        bind_tooltip(self._start_over, self.start_over_tooltip)

    # -- state -----------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, *, paused: bool = False) -> None:
        """Set the visual state; ``paused`` marks a play-state that resumes."""
        if state not in _STATES:
            raise ValueError(f"unknown transport state: {state!r}")
        self._state = state
        self._paused = paused
        self._render()

    def tooltip(self) -> str:
        """The tooltip text for the current state — what the control will do next."""
        if self._state == UNAVAILABLE:
            return UNAVAILABLE_TOOLTIP
        if self._state == PREPARING:
            return PREPARING_TOOLTIP
        if self._state == PAUSE:
            return PAUSE_TOOLTIP
        return RESUME_TOOLTIP if self._paused else PLAY_FROM_TOP_TOOLTIP

    def start_over_state(self) -> str:
        """The Start over surface: ``enabled`` once playback has begun
        (PLAYING or PAUSED), ``hidden`` before then (FR-004, Clarification Q3)."""
        if self._state == PAUSE or (self._state == PLAY and self._paused):
            return "enabled"
        return "hidden"

    def start_over_tooltip(self) -> str:
        return START_OVER_TOOLTIP

    def _render(self) -> None:
        if self._state == PAUSE:
            self._icon, self._enabled, text = "pause", True, "Pause"
        elif self._state == PLAY:
            self._icon, self._enabled, text = "play", True, "Play"
        else:  # unavailable and preparing are disabled ▶
            self._icon, self._enabled, text = "play", False, "Play"
        fg = theme.Palette.text if self._enabled else theme.Palette.disabled_text
        self._button.configure(
            image=icons.get_icon(self._icon, disabled=not self._enabled),
            text=text, state="normal" if self._enabled else "disabled",
            fg=fg, activeforeground=fg,
            disabledforeground=theme.Palette.disabled_text,
        )
        if self.start_over_state() == "enabled":
            self._start_over.configure(state="normal", fg=theme.Palette.text,
                                       activeforeground=theme.Palette.text)
            self._start_over.pack(anchor="w")
        else:
            self._start_over.configure(state="disabled", fg=theme.Palette.disabled_text,
                                       activeforeground=theme.Palette.disabled_text)
            self._start_over.pack_forget()

    def _on_start_over(self) -> None:
        if self._on_start_over_cb is not None:
            self._on_start_over_cb()

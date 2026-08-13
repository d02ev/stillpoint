"""The two fixed-role channel rows: "Background music" and "Voice".

This module owns both the *pure* channel-state derivation (display-free, so it
is unit-testable headlessly per research Decision 6) and the thin `ChannelRow`
widget that renders an empty or loaded channel.

Channel state comes from the project model's movie (adaptation of spec 002's
manifest asset roles): role ``"music"`` maps to ``movie.audio`` and role
``"voice"`` maps to ``movie.voice``. A role that is recorded is "loaded" even if
the file is missing on disk (spec Edge Cases) — the frame never silently reverts
a loaded channel to empty.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from .. import theme

# Fixed roles in the fixed order the spec mandates (FR-010).
MUSIC_ROLE = "music"
VOICE_ROLE = "voice"
CHANNEL_TITLES = {MUSIC_ROLE: "Background music", VOICE_ROLE: "Voice"}


def audio_display_name(item) -> str:
    """The audio's display name: the basename of its recorded filename."""
    return Path(item.filename).name


def channel_state(movie, role: str) -> tuple[str, str | None]:
    """Derive a channel's display state from the project's movie.

    Returns ``("empty", None)`` or ``("loaded", display_name)``. Pure: no Tk,
    no I/O, never touches the disk.
    """
    if role == MUSIC_ROLE:
        item = movie.audio
    elif role == VOICE_ROLE:
        item = movie.voice
    else:
        raise ValueError(f"unknown channel role: {role!r}")
    if item is None:
        return "empty", None
    return "loaded", audio_display_name(item)


class ChannelRow(tk.Frame):
    """One channel: a title plus either the empty-state actions, the name, or
    the importing progress line.

    ``on_download`` is only wired for the music role; ``on_import`` for both.
    A loaded row is clickable and calls ``on_click(role)``.
    """

    def __init__(self, master, role: str, on_download=None, on_import=None, on_click=None, **kwargs):
        super().__init__(master, bg=theme.Palette.panel, **kwargs)
        if role not in CHANNEL_TITLES:
            raise ValueError(f"unknown channel role: {role!r}")
        self._role = role
        self._on_download = on_download
        self._on_import = on_import
        self._on_click = on_click
        self._state = "empty"

        self._title = tk.Label(self, text=CHANNEL_TITLES[role], bg=theme.Palette.panel,
                               fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold"))
        self._title.pack(anchor="w", padx=theme.PAD, pady=(theme.PAD_SMALL, 4))
        self._body = tk.Frame(self, bg=theme.Palette.panel)
        self._body.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD_SMALL))
        self._set_state("empty", None)

    # -- state -----------------------------------------------------------

    def set_state(self, state: str, display_name: str | None) -> None:
        if state not in ("empty", "importing", "loaded"):
            raise ValueError(f"unknown channel state: {state!r}")
        self._state = state
        self._set_state(state, display_name)

    def _set_state(self, state: str, display_name: str | None) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        if state == "loaded":
            self._render_loaded(display_name or "audio")
        elif state == "importing":
            self._render_importing(display_name or "")
        else:
            self._render_empty()

    def state(self) -> str:
        return self._state

    # -- rendering --------------------------------------------------------

    def _render_empty(self) -> None:
        if self._role == MUSIC_ROLE:
            self._action_button(self._body, "download", "Download from YouTube", self._on_download)
        self._action_button(self._body, "import", "Import from computer", self._on_import)

    def _render_importing(self, detail: str) -> None:
        tk.Label(self._body, text=detail, bg=theme.Palette.panel, fg=theme.Palette.text,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE), justify="left", anchor="w"
                 ).pack(fill="x", pady=2)

    def _render_loaded(self, display_name: str) -> None:
        row = tk.Frame(self._body, bg=theme.Palette.panel_light, cursor="hand2")
        row.pack(fill="x", pady=2)
        row.bind("<Button-1>", lambda _e: self._on_click(self._role) if self._on_click else None)
        name = tk.Label(row, text=display_name, bg=theme.Palette.panel_light, fg=theme.Palette.text,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"), anchor="w")
        name.pack(side="left", padx=theme.PAD_SMALL, pady=theme.PAD_SMALL)
        name.bind("<Button-1>", lambda _e: self._on_click(self._role) if self._on_click else None)
        hint = tk.Label(row, text="Adjust sound", bg=theme.Palette.panel_light,
                        fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        hint.pack(side="right", padx=theme.PAD_SMALL)
        hint.bind("<Button-1>", lambda _e: self._on_click(self._role) if self._on_click else None)

    def _action_button(self, parent, icon_name: str, label: str, command) -> None:
        from . import icons

        btn = tk.Button(
            parent, text=label, command=command,
            image=icons.get_icon(icon_name, color=theme.Palette.text),
            compound="left", bg=theme.Palette.panel_light, fg=theme.Palette.text,
            activebackground=theme.Palette.accent_soft, activeforeground=theme.Palette.text,
            relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL, anchor="w",
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        )
        btn.pack(fill="x", pady=2)

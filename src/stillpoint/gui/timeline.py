"""Timeline widget: a horizontal strip of image thumbnails with durations."""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from .. import model as model_mod, theme

THUMB_H = 64
THUMB_W = 96
GAP = 6


class Timeline(tk.Frame):
    """Draws one thumb per image; clicking selects, callback gets the index."""

    def __init__(self, master, on_select=None, **kwargs):
        super().__init__(master, bg=theme.Palette.panel, **kwargs)
        self._on_select = on_select
        self._canvas = tk.Canvas(self, bg=theme.Palette.panel, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._project: model_mod.Project | None = None
        self._selected: int | None = None
        self._thumbs: list[ImageTk.PhotoImage] = []
        self._boxes: list[int] = []  # canvas item ids per thumb
        self._canvas.bind("<Button-1>", self._click)
        self._canvas.bind("<Configure>", lambda _e: self.redraw())

    def set_project(self, project: model_mod.Project | None, selected: int | None = None) -> None:
        self._project = project
        self._selected = selected
        self.redraw()

    def selected_index(self) -> int | None:
        return self._selected

    def select(self, index: int | None) -> None:
        self._selected = index
        self.redraw()

    def _click(self, event: tk.Event) -> None:
        if not self._project or not self._project.images:
            return
        index = int(event.x // (THUMB_W + GAP))
        if 0 <= index < len(self._project.images):
            self._selected = index
            self.redraw()
            if self._on_select:
                self._on_select(index)

    def redraw(self) -> None:
        self._canvas.delete("all")
        self._thumbs = []
        self._boxes = []
        if not self._project or not self._project.images:
            self._canvas.create_text(
                THUMB_W * 2, THUMB_H // 2 + 8, text="no images yet — add some below",
                fill=theme.Palette.text_faint, font=(theme.FONT_FAMILY, theme.FONT_SIZE))
            return
        for index, item in enumerate(self._project.images):
            x = index * (THUMB_W + GAP)
            y = 4
            path = self._project.media_file(item)
            if path.exists():
                with Image.open(path) as img:
                    img = img.convert("RGB")
                    img.thumbnail((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._thumbs.append(photo)
                self._canvas.create_image(x + GAP // 2, y, image=photo, anchor="nw")
            else:
                self._canvas.create_rectangle(x + GAP // 2, y, x + THUMB_W, y + THUMB_H,
                                              fill=theme.Palette.panel_light, outline=theme.Palette.border)
            box = self._canvas.create_rectangle(
                x, y, x + THUMB_W + GAP, y + THUMB_H,
                outline=(theme.Palette.accent if index == self._selected else theme.Palette.border),
                width=(3 if index == self._selected else 1))
            self._boxes.append(box)
            self._canvas.create_text(
                x + THUMB_W // 2, y + THUMB_H + 10, text=f"{item.duration:.1f}s",
                fill=(theme.Palette.text if index == self._selected else theme.Palette.text_dim),
                font=(theme.FONT_FAMILY, theme.FONT_SIZE - 1))

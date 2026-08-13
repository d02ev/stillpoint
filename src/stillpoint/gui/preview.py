"""Preview widget: a picture-in-picture still of the selected image with the
audio waveform drawn along the bottom edge."""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from .. import model as model_mod, theme


class Preview(tk.Frame):
    """Shows the selected image cover-fitted into the preview area."""

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=theme.Palette.background, **kwargs)
        self._canvas = tk.Canvas(self, bg=theme.Palette.background, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._image_ref = None
        self._project: model_mod.Project | None = None
        self._index: int | None = None
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_project(self, project: model_mod.Project | None) -> None:
        self._project = project
        self._index = None
        self.redraw()

    def show_index(self, index: int | None) -> None:
        self._index = index
        self.redraw()

    def _current_item(self) -> model_mod.MediaItem | None:
        if not self._project or not self._project.images:
            return None
        if self._index is None or not 0 <= self._index < len(self._project.images):
            return None
        return self._project.images[self._index]

    def redraw(self) -> None:
        self._canvas.delete("all")
        width = self._canvas.winfo_width() or 320
        height = self._canvas.winfo_height() or 180
        canvas_w, canvas_h = self._canvas_size()
        draw_w, draw_h = self._fit(canvas_w, canvas_h, width, height)
        x0 = (width - draw_w) // 2
        y0 = (height - draw_h) // 2
        self._canvas.create_rectangle(0, 0, width, height,
                                      fill=theme.Palette.preview_overlay, outline="")

        item = self._current_item()
        if item and self._project is not None:
            path = self._project.media_file(item)
            if path.exists():
                with Image.open(path) as img:
                    img = img.convert("RGB")
                    thumb = img.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
                self._image_ref = ImageTk.PhotoImage(thumb)
                self._canvas.create_image(x0, y0, image=self._image_ref, anchor="nw")
                # Pillarbox frame so a silent image doesn't blend into the bg.
                self._canvas.create_rectangle(x0, y0, x0 + draw_w, y0 + draw_h,
                                              outline=theme.Palette.border, width=1)

        self._draw_waveform_overlay(draw_w, draw_h, x0, y0, canvas_w, canvas_h)

    def _canvas_size(self) -> tuple[int, int]:
        from .. import render

        if not self._project:
            return render.CANVAS_SIZES[model_mod.RATIO_WIDE]
        return render.CANVAS_SIZES.get(self._project.movie.ratio, render.CANVAS_SIZES[model_mod.RATIO_WIDE])

    @staticmethod
    def _fit(canvas_w: int, canvas_h: int, box_w: int, box_h: int) -> tuple[int, int]:
        """Largest draw size preserving the canvas aspect ratio inside the box."""
        scale = min(box_w / canvas_w, box_h / canvas_h)
        return max(1, int(canvas_w * scale)), max(1, int(canvas_h * scale))

    def _draw_waveform_overlay(self, draw_w: int, draw_h: int, x0: int, y0: int, canvas_w: int, canvas_h: int) -> None:
        if not self._project or not self._project.movie.audio:
            return
        # Overlay the waveform as a translucent strip along the bottom.
        bar_h = max(8, draw_h // 6)
        bar_y = y0 + draw_h - bar_h
        n = min(96, max(8, draw_w // 3))
        peaks = self._project.audio_peaks or [0.0] * n
        if len(peaks) < n:
            peaks = (peaks + [0.0] * n)[:n]
        step = draw_w / n
        self._canvas.create_rectangle(x0, bar_y, x0 + draw_w, bar_y + bar_h,
                                      fill="#000000", outline="")
        for i, peak in enumerate(peaks):
            h = max(2, int(peak * (bar_h - 4)))
            cx = x0 + int(i * step) + int(step / 2)
            self._canvas.create_line(cx, bar_y + bar_h // 2, cx, bar_y + bar_h // 2 - h,
                                     fill=theme.Palette.accent, width=1)

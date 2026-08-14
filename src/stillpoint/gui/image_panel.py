"""The real image panel (spec 007): find, preview, and download a still
background picture from Pexels into the project.

Mirrors ``DownloadPanel``: a thin Tkinter widget that never touches the model
or the network itself (Constitution VI). Search and download each run as a
worker daemon thread polled with ``root.after`` (one job at a time, FR-007/014);
the preview pop-out runs its own independent worker and never blocks the panel
(FR-008/009/010). Every user-facing string is imported from :mod:`pexels`,
never re-typed (Constitution I).
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from .. import pexels, theme
from .panels import PANEL_IMAGE, PANEL_TITLES, _PanelFrame
from .workers import (
    ImageDownloadEvent,
    ImageDownloadWorker,
    PreviewEvent,
    PreviewImageWorker,
    SearchEvent,
    SearchWorker,
)

_POLL_MS = 100
_THUMB_W = 224
_THUMB_H = 126
_PREVIEW_SCALE = 0.8
_PICTURE_ROW_THUMB = (72, 41)  # small 16:9 thumbnail in the main-area row


def _cover_fit(image: Image.Image, width: int, height: int) -> Image.Image:
    """Center-crop ``image`` to ``width`` x ``height`` without distortion."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


class ImagePanel(_PanelFrame):
    def __init__(self, master, on_choose=None, **kwargs):
        super().__init__(master, PANEL_TITLES[PANEL_IMAGE], **kwargs)
        self._project = None
        self._on_choose = on_choose
        self._worker = None
        self._poll_id = None
        self._busy = False
        self._result_rows: list[tk.Frame] = []
        self._download_buttons: list[tk.Button] = []
        self._preview_buttons: list[tk.Button] = []
        self._thumb_images: list[ImageTk.PhotoImage] = []
        self._library_rows: list[tuple[str, tk.Widget]] = []
        self._library_empty_label = None
        self._preview_worker = None
        self._preview_poll_id = None
        self._preview_toplevel = None
        self._preview_message = None
        self._preview_image_label = None
        self._preview_photo_image = None
        self._build_controls()

    # -- construction ----------------------------------------------------------

    def _build_controls(self) -> None:
        search_row = tk.Frame(self._body, bg=theme.Palette.panel)
        search_row.pack(fill="x", pady=(0, theme.PAD_SMALL))
        self._search_var = tk.StringVar()
        self._search_var.set(pexels.SEARCH_PLACEHOLDER)
        self._search_entry = tk.Entry(
            search_row, textvariable=self._search_var,
            bg=theme.Palette.panel_light, fg=theme.Palette.text,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
            relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
        )
        self._search_entry.pack(side="left", fill="x", expand=True)
        self._search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self._search_entry.bind("<Return>", lambda _e: self._on_search())
        self._search_btn = tk.Button(
            search_row, text=pexels.SEARCH_BUTTON_LABEL, command=self._on_search,
            bg=theme.Palette.accent, fg="#FFFFFF", activebackground=theme.Palette.accent_hover,
            activeforeground="#FFFFFF", relief="flat", highlightthickness=0,
            padx=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"),
        )
        self._search_btn.pack(side="left", padx=(theme.PAD_SMALL, 0))

        self._status = tk.Label(
            self._body, text="", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE), justify="left", anchor="w",
            wraplength=self.width - theme.PAD * 2,
        )
        self._status.pack(fill="x", pady=(0, theme.PAD_SMALL))

        current_section = tk.Frame(self._body, bg=theme.Palette.panel)
        current_section.pack(fill="x", pady=(0, theme.PAD_SMALL))
        tk.Label(current_section, text=pexels.CURRENT_PICTURE_TITLE,
                 bg=theme.Palette.panel, fg=theme.Palette.text,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold")
                 ).pack(anchor="w", pady=(0, 4))
        self._current_message = tk.Label(
            current_section, text=pexels.CURRENT_PICTURE_EMPTY, bg=theme.Palette.panel,
            fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
            justify="left", anchor="w",
        )
        self._current_message.pack(anchor="w")
        self._current_image_label = tk.Label(current_section, bg=theme.Palette.panel_light)
        self._current_image_ref = None

        self._canvas = tk.Canvas(self._body, bg=theme.Palette.panel, highlightthickness=0)
        scrollbar = tk.Scrollbar(self._body, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._canvas.pack(fill="both", expand=True)
        self._scroll_body = tk.Frame(self._canvas, bg=theme.Palette.panel)
        self._window = self._canvas.create_window((0, 0), window=self._scroll_body, anchor="nw")
        self._scroll_body.bind(
            "<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )
        self._canvas.bind(
            "<Configure>", lambda _e: self._canvas.itemconfigure(self._window, width=self._canvas.winfo_width())
        )
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)

        self._results_holder = tk.Frame(self._scroll_body, bg=theme.Palette.panel)
        self._results_holder.pack(fill="x")

        self._library = tk.Frame(self._scroll_body, bg=theme.Palette.panel)
        self._library.pack(fill="x", pady=(theme.PAD_SMALL, 0))
        tk.Label(self._library, text=pexels.LIBRARY_TITLE, bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold")
                 ).pack(anchor="w", pady=(0, 4))

    # -- project wiring --------------------------------------------------------

    def set_project(self, project) -> None:
        self._project = project
        self.refresh_library()

    def _current_background(self) -> str | None:
        """The background filename, only when exactly one image is set (007)."""
        item = self._current_item()
        return item.filename if item is not None else None

    def _current_item(self):
        """The single recorded image MediaItem, or ``None`` when none is set."""
        if self._project is None:
            return None
        images = self._project.images
        if len(images) == 1 and images[0].kind == "image":
            return images[0]
        return None

    def refresh_library(self) -> None:
        """Re-derive the current-picture card and the downloaded-images list."""
        self._refresh_current_picture()
        for _name, row in self._library_rows:
            row.destroy()
        self._library_rows = []
        if self._library_empty_label is not None:
            self._library_empty_label.destroy()
            self._library_empty_label = None
        if self._project is None:
            return
        current = self._current_background()
        filenames = pexels.list_downloaded_images(self._project)
        for filename in filenames:
            is_background = filename == current
            text = filename + (pexels.BACKGROUND_MARKER if is_background else "")
            row = tk.Label(
                self._library, text=text, bg=theme.Palette.panel_light,
                fg=theme.Palette.accent if is_background else theme.Palette.text,
                font=(theme.FONT_FAMILY, theme.FONT_SIZE), anchor="w", cursor="hand2",
                padx=theme.PAD_SMALL, pady=theme.PAD_SMALL,
            )
            row.pack(fill="x", pady=2)
            row.bind("<Button-1>", lambda _e, n=filename: self._on_library_click(n))
            self._library_rows.append((filename, row))
        if not filenames:
            self._library_empty_label = tk.Label(
                self._library, text=pexels.LIBRARY_EMPTY_MESSAGE, bg=theme.Palette.panel,
                fg=theme.Palette.text_dim, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
                justify="left", anchor="w", wraplength=self.width - theme.PAD * 2,
            )
            self._library_empty_label.pack(anchor="w", pady=theme.PAD_SMALL)

    def _refresh_current_picture(self) -> None:
        """Show the recorded background picture (or a plain unset line)."""
        self._current_image_label.configure(image="")
        self._current_image_ref = None
        self._current_image_label.pack_forget()
        item = self._current_item()
        if item is None or self._project is None:
            self._current_message.configure(text=pexels.CURRENT_PICTURE_EMPTY)
            self._current_message.pack(anchor="w")
            return
        path = self._project.media_file(item)
        try:
            with Image.open(path) as img:
                display = self._cover_thumb(img.convert("RGB"))
        except Exception:  # noqa: BLE001 - an unreadable or missing picture shows as unset
            self._current_message.configure(text=pexels.CURRENT_PICTURE_EMPTY)
            self._current_message.pack(anchor="w")
            return
        self._current_message.configure(text="")
        self._current_message.pack_forget()
        self._current_image_ref = ImageTk.PhotoImage(display)
        self._current_image_label.configure(image=self._current_image_ref)
        self._current_image_label.pack(fill="x")

    def _cover_thumb(self, image: Image.Image) -> Image.Image:
        """A 16:9 cover-crop of ``image`` sized to the panel's inner width."""
        width = max(1, self.width - theme.PAD * 2)
        height = max(1, round(width * 9 / 16))
        return _cover_fit(image, width, height)

    def _on_library_click(self, filename: str) -> None:
        if filename == self._current_background():
            return
        if self._on_choose is not None:
            self._on_choose(filename)
        self.refresh_library()

    # -- search ---------------------------------------------------------------

    def _on_search(self) -> None:
        query = self._search_var.get().strip()
        if not query or query == pexels.SEARCH_PLACEHOLDER:
            self._clear_results()
            self._status.configure(text=pexels.SEARCH_PLACEHOLDER)
            return
        if self._busy:
            self._status.configure(text=pexels.WAIT_FOR_JOB_MESSAGE)
            return
        self._clear_results()
        self._set_busy(True)
        self._status.configure(text=pexels.SEARCHING_MESSAGE)
        self._worker = SearchWorker(query, key=None)
        self._worker.start()
        self._poll()

    def _on_search_event(self, event: SearchEvent) -> None:
        if event.state == "searching":
            self._status.configure(text=event.detail)
            return
        self._clear_results()
        if event.state == "done":
            self._render_results(event.photos, event.thumbs)
            self._status.configure(text="")
        elif event.state == "empty":
            self._status.configure(text=event.detail)
        elif event.state == "error":
            self._status.configure(text=event.detail)
        self._set_busy(False)
        self._worker = None

    def _render_results(self, photos, thumbs) -> None:
        for photo in photos:
            row = tk.Frame(self._results_holder, bg=theme.Palette.panel)
            row.pack(fill="x", pady=(0, theme.PAD_SMALL))
            thumb = thumbs.get(photo.id)
            if thumb is not None:
                display = thumb.resize((_THUMB_W, _THUMB_H), Image.Resampling.LANCZOS)
                photo_image = ImageTk.PhotoImage(display)
                self._thumb_images.append(photo_image)
                thumb_label = tk.Label(row, image=photo_image, bg=theme.Palette.panel)
                thumb_label.image = photo_image
                thumb_label.pack(fill="x")
            buttons = tk.Frame(row, bg=theme.Palette.panel)
            buttons.pack(fill="x", pady=(theme.PAD_SMALL, 0))
            preview_btn = tk.Button(
                buttons, text=pexels.RESULT_PREVIEW_LABEL, command=lambda p=photo: self._on_preview(p),
                bg=theme.Palette.panel_light, fg=theme.Palette.text,
                activebackground=theme.Palette.accent_soft, activeforeground=theme.Palette.text,
                relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
                padx=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE),
            )
            preview_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
            download_btn = tk.Button(
                buttons, text=pexels.RESULT_DOWNLOAD_LABEL, command=lambda p=photo: self._on_download(p),
                bg=theme.Palette.accent, fg="#FFFFFF", activebackground=theme.Palette.accent_hover,
                activeforeground="#FFFFFF", relief="flat", highlightthickness=0,
                padx=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"),
            )
            download_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))
            self._preview_buttons.append(preview_btn)
            self._download_buttons.append(download_btn)
            self._result_rows.append(row)

    # -- download ---------------------------------------------------------------

    def _on_download(self, photo) -> None:
        if self._busy:
            self._status.configure(text=pexels.WAIT_FOR_JOB_MESSAGE)
            return
        self._set_busy(True)
        self._status.configure(text=pexels.DOWNLOADING_MESSAGE)
        self._worker = ImageDownloadWorker(self._project, photo)
        self._worker.start()
        self._poll()

    def _on_download_event(self, event: ImageDownloadEvent) -> None:
        if event.state == "downloading":
            self._status.configure(text=event.detail)
            return
        if event.state == "done":
            self._status.configure(text=event.detail)
            if self._on_choose is not None:
                self._on_choose(event.value)
            self.refresh_library()
        elif event.state == "error":
            self._status.configure(text=event.detail)
        self._set_busy(False)
        self._worker = None

    # -- preview pop-out --------------------------------------------------------

    def _on_preview(self, photo) -> None:
        if self._preview_worker is not None:
            if self._preview_poll_id is not None:
                self.after_cancel(self._preview_poll_id)
                self._preview_poll_id = None
            self._close_preview()
        self._preview_worker = PreviewImageWorker(photo)
        self._preview_worker.start()
        self._preview_poll()

    def _on_preview_event(self, event: PreviewEvent) -> None:
        if event.state == "loading":
            self._preview_loading(event.detail)
            return
        self._preview_worker = None
        if event.state == "shown":
            self._preview_shown(event.value)
        else:
            self._preview_failed(event.detail)

    def _preview_loading(self, detail: str) -> None:
        self._ensure_preview_toplevel()
        self._preview_message.configure(text=detail)
        self._preview_message.pack(padx=theme.PAD_LARGE, pady=theme.PAD_LARGE)
        self._preview_image_label.pack_forget()

    def _preview_shown(self, image) -> None:
        self._ensure_preview_toplevel()
        self._preview_message.pack_forget()
        display = self._fit_image(image)
        self._preview_photo_image = ImageTk.PhotoImage(display)
        self._preview_image_label.configure(image=self._preview_photo_image)
        self._preview_image_label.pack(padx=theme.PAD_LARGE, pady=theme.PAD_LARGE)

    def _preview_failed(self, detail: str) -> None:
        self._ensure_preview_toplevel()
        self._preview_message.configure(text=detail)
        self._preview_message.pack(padx=theme.PAD_LARGE, pady=theme.PAD_LARGE)
        self._preview_image_label.pack_forget()

    def _ensure_preview_toplevel(self) -> None:
        if self._preview_toplevel is not None and self._preview_toplevel.winfo_exists():
            return
        self._preview_toplevel = tk.Toplevel(self)
        self._preview_toplevel.title(pexels.PREVIEW_TITLE)
        self._preview_message = tk.Label(
            self._preview_toplevel, text="", bg=theme.Palette.background, fg=theme.Palette.text,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE), justify="left", wraplength=560,
        )
        self._preview_message.pack(padx=theme.PAD_LARGE, pady=theme.PAD_LARGE)
        self._preview_image_label = tk.Label(self._preview_toplevel, bg=theme.Palette.background)

    def _fit_image(self, image):
        max_w = int(self.winfo_screenwidth() * _PREVIEW_SCALE)
        max_h = int(self.winfo_screenheight() * _PREVIEW_SCALE)
        scale = min(1.0, max_w / image.width, max_h / image.height)
        if scale >= 1.0:
            return image
        return image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    def _close_preview(self) -> None:
        if self._preview_toplevel is not None and self._preview_toplevel.winfo_exists():
            self._preview_toplevel.destroy()
        self._preview_toplevel = None
        self._preview_message = None
        self._preview_image_label = None
        self._preview_photo_image = None

    # -- job polling -------------------------------------------------------------

    def _poll(self) -> None:
        worker = self._worker
        if worker is None:
            self._poll_id = None
            return
        while True:
            event = worker.poll()
            if event is None:
                break
            if isinstance(event, SearchEvent):
                self._on_search_event(event)
            else:
                self._on_download_event(event)
            if self._worker is None:
                break
        if self._worker is not None:
            self._poll_id = self.after(_POLL_MS, self._poll)
        else:
            self._poll_id = None

    def _preview_poll(self) -> None:
        worker = self._preview_worker
        if worker is None:
            self._preview_poll_id = None
            return
        while True:
            event = worker.poll()
            if event is None:
                break
            self._on_preview_event(event)
            if self._preview_worker is None:
                break
        if self._preview_worker is not None:
            self._preview_poll_id = self.after(_POLL_MS, self._preview_poll)
        else:
            self._preview_poll_id = None

    # -- small helpers -------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._search_btn.configure(state=state)
        for btn in self._download_buttons:
            btn.configure(state=state)

    def _clear_results(self) -> None:
        for child in self._results_holder.winfo_children():
            child.destroy()
        self._result_rows = []
        self._download_buttons = []
        self._preview_buttons = []
        self._thumb_images = []

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_search_focus_in(self, _event=None) -> None:
        if self._search_var.get() == pexels.SEARCH_PLACEHOLDER:
            self._search_entry.delete(0, "end")


class PictureRow(tk.Frame):
    """The main-area "Background picture" row (007): mirrors ``ChannelRow``.

    Loaded shows the picture's filename with a small 16:9 thumbnail and a
    "Choose another" hint; empty shows a single "Find a picture" button. Any
    click opens the image panel through ``on_click``. Report-only — the editor
    owns model writes.
    """

    def __init__(self, master, on_click=None, **kwargs):
        super().__init__(master, bg=theme.Palette.panel, **kwargs)
        self._on_click = on_click
        self._project = None
        self._image_ref = None
        tk.Label(self, text=pexels.PICTURE_ROW_TITLE, bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold")
                 ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD_SMALL, 4))
        self._body = tk.Frame(self, bg=theme.Palette.panel)
        self._body.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD_SMALL))

    def set_project(self, project) -> None:
        self._project = project
        self._set_state()

    def _set_state(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        self._image_ref = None
        item = self._current_item()
        if item is None:
            self._render_empty()
        else:
            self._render_loaded(item)

    def _current_item(self):
        if self._project is None:
            return None
        images = self._project.images
        if len(images) == 1 and images[0].kind == "image":
            return images[0]
        return None

    def _render_empty(self) -> None:
        from . import icons

        tk.Button(
            self._body, text=pexels.PICTURE_ROW_EMPTY_ACTION,
            command=self._click,
            image=icons.get_icon("picture", color=theme.Palette.text),
            compound="left", bg=theme.Palette.panel_light, fg=theme.Palette.text,
            activebackground=theme.Palette.accent_soft, activeforeground=theme.Palette.text,
            relief="flat", highlightthickness=1, highlightbackground=theme.Palette.border,
            padx=theme.PAD, pady=theme.PAD_SMALL, anchor="w",
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        ).pack(fill="x", pady=2)

    def _render_loaded(self, item) -> None:
        row = tk.Frame(self._body, bg=theme.Palette.panel_light, cursor="hand2")
        row.pack(fill="x", pady=2)
        row.bind("<Button-1>", lambda _e: self._click())
        thumb = self._thumbnail(item)
        if thumb is not None:
            self._image_ref = thumb
            img = tk.Label(row, image=self._image_ref, bg=theme.Palette.panel_light)
            img.pack(side="left", padx=theme.PAD_SMALL, pady=theme.PAD_SMALL)
            img.bind("<Button-1>", lambda _e: self._click())
        name = tk.Label(row, text=item.filename, bg=theme.Palette.panel_light,
                        fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"),
                        anchor="w")
        name.pack(side="left", padx=theme.PAD_SMALL, pady=theme.PAD_SMALL)
        name.bind("<Button-1>", lambda _e: self._click())
        hint = tk.Label(row, text=pexels.PICTURE_ROW_CHOOSE_HINT,
                        bg=theme.Palette.panel_light, fg=theme.Palette.text_dim,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        hint.pack(side="right", padx=theme.PAD_SMALL)
        hint.bind("<Button-1>", lambda _e: self._click())

    def _thumbnail(self, item):
        """A small 16:9 thumbnail of the recorded picture, or ``None``."""
        if self._project is None:
            return None
        try:
            with Image.open(self._project.media_file(item)) as img:
                thumb = _cover_fit(img.convert("RGB"), *_PICTURE_ROW_THUMB)
            return ImageTk.PhotoImage(thumb)
        except Exception:  # noqa: BLE001 - a missing/unreadable picture shows name only
            return None

    def _click(self) -> None:
        if self._on_click is not None:
            self._on_click()

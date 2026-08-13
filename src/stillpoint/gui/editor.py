"""Editor screen: preview, timeline and controls for the current project."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .. import dialogs, media, model as model_mod, names, render, theme
from .preview import Preview
from .timeline import Timeline
from .waveform import Waveform
from .workers import RenderWorker


class EditorScreen(tk.Frame):
    def __init__(self, app, **kwargs):
        super().__init__(app.root, bg=theme.Palette.background, **kwargs)
        self.app = app
        self._worker: RenderWorker | None = None

        self._header = tk.Frame(self, bg=theme.Palette.panel)
        self._header.pack(fill="x")
        tk.Button(
            self._header, text="‹ Home", command=self._go_home,
            bg=theme.Palette.panel_light, fg=theme.Palette.text, relief="flat",
            highlightthickness=0, padx=theme.PAD_SMALL, pady=2,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE),
        ).pack(side="left", padx=theme.PAD, pady=theme.PAD_SMALL)
        self._title_label = tk.Label(
            self._header, text="", bg=theme.Palette.panel, fg=theme.Palette.text,
            font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold"))
        self._title_label.pack(side="left", padx=theme.PAD_SMALL)
        self._progress = ttk.Progressbar(self._header, length=160, mode="determinate")
        self._progress.pack(side="right", padx=theme.PAD, pady=theme.PAD_SMALL)
        self._export = tk.Button(
            self._header, text="Export…", command=self._export,
            bg=theme.Palette.accent, fg="#FFFFFF", relief="flat", activebackground=theme.Palette.accent_hover,
            padx=theme.PAD, pady=theme.PAD_SMALL, font=(theme.FONT_FAMILY, theme.FONT_SIZE, "bold"))
        self._export.pack(side="right", padx=(theme.PAD, theme.PAD_SMALL), pady=theme.PAD_SMALL)

        body = tk.Frame(self, bg=theme.Palette.background)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD_SMALL)

        self._preview = Preview(body)
        self._preview.pack(side="left", fill="both", expand=True, padx=(0, theme.PAD))

        self._controls = tk.Frame(body, bg=theme.Palette.panel, width=240)
        self._controls.pack(side="right", fill="y", padx=(0, 0))
        self._controls.pack_propagate(False)
        self._build_controls()

        self._timeline = Timeline(body, on_select=lambda i: self._preview.show_index(i))
        self._timeline.pack(side="bottom", fill="x", pady=(theme.PAD_SMALL, 0), ipady=4)
        body.pack_propagate(False)

        self.after(150, self._poll_render)

    # -- controls --------------------------------------------------------

    def _build_controls(self) -> None:
        pad = theme.PAD
        self._controls.configure(bg=theme.Palette.panel)
        tk.Label(self._controls, text="Images", bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", padx=pad, pady=(pad, 4))
        self._add_images = tk.Button(self._controls, text="Add Images…", command=self._add_images,
                                     bg=theme.Palette.panel_light, fg=theme.Palette.text, relief="flat",
                                     font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        self._add_images.grid(row=1, column=0, columnspan=2, sticky="we", padx=pad, pady=4)
        self._remove = tk.Button(self._controls, text="Remove selected", command=self._remove_selected,
                                 bg=theme.Palette.panel_light, fg=theme.Palette.danger, relief="flat",
                                 font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        self._remove.grid(row=2, column=0, sticky="we", padx=(pad, 2), pady=4)
        self._move_row = tk.Frame(self._controls, bg=theme.Palette.panel)
        self._move_row.grid(row=3, column=0, columnspan=2, sticky="we", padx=pad)
        tk.Button(self._move_row, text="◀", command=lambda: self._move(-1),
                  bg=theme.Palette.panel_light, fg=theme.Palette.text, relief="flat",
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE)).pack(side="left", fill="x", expand=True, padx=(0, 2))
        tk.Button(self._move_row, text="▶", command=lambda: self._move(1),
                  bg=theme.Palette.panel_light, fg=theme.Palette.text, relief="flat",
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE)).pack(side="left", fill="x", expand=True, padx=(2, 0))

        tk.Label(self._controls, text="Movie", bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold")
                 ).grid(row=4, column=0, columnspan=2, sticky="w", padx=pad, pady=(pad, 4))
        self._image_duration = tk.DoubleVar(value=5.0)
        tk.Label(self._controls, text="Image seconds", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=5, column=0, sticky="w", padx=pad)
        tk.Spinbox(self._controls, from_=0.5, to=60.0, increment=0.5, width=6,
                   textvariable=self._image_duration, command=self._on_default_duration,
                   bg=theme.Palette.panel_light, fg=theme.Palette.text,
                   buttonbackground=theme.Palette.panel_light).grid(row=5, column=1, sticky="e", padx=pad)

        self._crossfade = tk.DoubleVar(value=0.0)
        tk.Label(self._controls, text="Crossfade (s)", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=6, column=0, sticky="w", padx=pad)
        tk.Scale(self._controls, from_=0.0, to=5.0, resolution=0.1, orient="horizontal",
                 variable=self._crossfade, command=lambda _v: self._on_crossfade(),
                 bg=theme.Palette.panel, fg=theme.Palette.text,
                 troughcolor=theme.Palette.panel_light, highlightthickness=0).grid(row=6, column=1, sticky="we", padx=pad)

        self._ratio = tk.StringVar(value=model_mod.RATIO_WIDE)
        tk.Label(self._controls, text="Aspect ratio", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=7, column=0, sticky="w", padx=pad)
        tk.OptionMenu(self._controls, self._ratio, *model_mod.RATIO_CHOICES, command=lambda _v: self._on_ratio()
                      ).grid(row=7, column=1, sticky="e", padx=pad)

        tk.Label(self._controls, text="Ambient audio", bg=theme.Palette.panel,
                 fg=theme.Palette.text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_TITLE, "bold")
                 ).grid(row=8, column=0, columnspan=2, sticky="w", padx=pad, pady=(pad, 4))
        self._audio_button = tk.Button(self._controls, text="Add audio…", command=self._add_audio,
                                       bg=theme.Palette.panel_light, fg=theme.Palette.text, relief="flat",
                                       font=(theme.FONT_FAMILY, theme.FONT_SIZE))
        self._audio_button.grid(row=9, column=0, columnspan=2, sticky="we", padx=pad, pady=4)
        self._volume = tk.DoubleVar(value=1.0)
        tk.Label(self._controls, text="Volume", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=10, column=0, sticky="w", padx=pad)
        tk.Scale(self._controls, from_=0.0, to=1.0, resolution=0.05, orient="horizontal",
                 variable=self._volume, command=lambda _v: self._on_audio_setting(),
                 bg=theme.Palette.panel, fg=theme.Palette.text,
                 troughcolor=theme.Palette.panel_light, highlightthickness=0).grid(row=10, column=1, sticky="we", padx=pad)
        self._fade_in = tk.DoubleVar(value=0.0)
        tk.Label(self._controls, text="Fade in (s)", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=11, column=0, sticky="w", padx=pad)
        tk.Scale(self._controls, from_=0.0, to=20.0, resolution=0.5, orient="horizontal",
                 variable=self._fade_in, command=lambda _v: self._on_audio_setting(),
                 bg=theme.Palette.panel, fg=theme.Palette.text,
                 troughcolor=theme.Palette.panel_light, highlightthickness=0).grid(row=11, column=1, sticky="we", padx=pad)
        self._fade_out = tk.DoubleVar(value=0.0)
        tk.Label(self._controls, text="Fade out (s)", bg=theme.Palette.panel, fg=theme.Palette.text_dim,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE)).grid(row=12, column=0, sticky="w", padx=pad)
        tk.Scale(self._controls, from_=0.0, to=20.0, resolution=0.5, orient="horizontal",
                 variable=self._fade_out, command=lambda _v: self._on_audio_setting(),
                 bg=theme.Palette.panel, fg=theme.Palette.text,
                 troughcolor=theme.Palette.panel_light, highlightthickness=0).grid(row=12, column=1, sticky="we", padx=pad)

        self._waveform = Waveform(self._controls)
        self._waveform.grid(row=13, column=0, columnspan=2, sticky="we", padx=pad, pady=pad)
        self._waveform.bind("<Configure>", self._waveform.on_resize)

    # -- project wiring ----------------------------------------------------

    def refresh(self) -> None:
        project = self.app.project
        if project is None:
            return
        self._title_label.configure(text=project.title)
        self._image_duration.set(project.image_duration)
        self._crossfade.set(project.movie.crossfade)
        self._ratio.set(project.movie.ratio)
        audio = project.movie.audio
        self._volume.set(audio.volume if audio else 1.0)
        self._fade_in.set(audio.fade_in if audio else 0.0)
        self._fade_out.set(audio.fade_out if audio else 0.0)
        self._audio_button.configure(text=(f"Audio: {audio.filename}" if audio else "Add audio…"))
        self._preview.set_project(project)
        self._timeline.set_project(project, selected=0)
        self._preview.show_index(0)
        self._waveform.set_peaks(project.audio_peaks)
        if audio and not project.audio_peaks:
            self._load_audio_peaks()

    def _go_home(self) -> None:
        self.app.show_home()

    # -- image actions -----------------------------------------------------

    def _add_images(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Add images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*")])
        project = self.app.project
        if not project or not files:
            return
        canvas_w, canvas_h = render.CANVAS_SIZES.get(project.movie.ratio, render.CANVAS_SIZES[model_mod.RATIO_WIDE])
        try:
            for path in files:
                destination = names.unique_filename(project.media_dir(), Path(path).stem, ".jpg")
                media.import_image(Path(path), project.media_dir() / destination, canvas_w, canvas_h)
                project.images.append(model_mod.MediaItem(kind="image", filename=destination,
                                                          duration=project.image_duration))
            project.save()
        except Exception as exc:  # noqa: BLE001
            dialogs.error("Stillpoint", f"Could not add the images:\n{exc}", parent=self)
        self._sync_timeline()

    def _remove_selected(self) -> None:
        index = self._timeline.selected_index()
        project = self.app.project
        if project is None or index is None or not 0 <= index < len(project.images):
            return
        project.images.pop(index)
        project.save()
        self._sync_timeline(selected=min(index, len(project.images) - 1))

    def _move(self, direction: int) -> None:
        index = self._timeline.selected_index()
        project = self.app.project
        if project is None or index is None:
            return
        new_index = index + direction
        if not 0 <= new_index < len(project.images):
            return
        project.images.insert(new_index, project.images.pop(index))
        project.save()
        self._sync_timeline(selected=new_index)

    def _on_default_duration(self) -> None:
        project = self.app.project
        if project is None:
            return
        project.image_duration = float(self._image_duration.get())
        project.save()
        self._timeline.redraw()

    def _on_crossfade(self) -> None:
        project = self.app.project
        if project is None:
            return
        project.movie.crossfade = float(self._crossfade.get())
        project.save()

    def _on_ratio(self) -> None:
        project = self.app.project
        if project is None:
            return
        project.movie.ratio = self._ratio.get()
        project.save()
        self._preview.redraw()

    def _sync_timeline(self, selected: int | None = None) -> None:
        self._timeline.set_project(self.app.project, selected=selected)
        self._preview.show_index(selected)

    # -- audio ------------------------------------------------------------

    def _add_audio(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Add ambient audio",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac"), ("All files", "*.*")])
        project = self.app.project
        if not project or not path:
            return
        source = Path(path)
        destination = names.unique_filename(project.media_dir(), source.stem, source.suffix.lower())
        import shutil

        shutil.copy2(source, project.media_dir() / destination)
        project.movie.audio = model_mod.MediaItem(kind="audio", filename=destination,
                                                  in_point=0.0, volume=1.0)
        project.save()
        self._audio_button.configure(text=f"Audio: {destination}")
        self._load_audio_peaks()

    def _on_audio_setting(self) -> None:
        project = self.app.project
        audio = project.movie.audio if project else None
        if audio is None:
            return
        audio.volume = float(self._volume.get())
        audio.fade_in = float(self._fade_in.get())
        audio.fade_out = float(self._fade_out.get())
        project.save()
        self._preview.redraw()

    def _load_audio_peaks(self) -> None:
        project = self.app.project
        audio = project.movie.audio if project else None
        if audio is None:
            return
        path = project.media_file(audio)

        def _work() -> None:
            try:
                peaks = media.waveform_peaks(path, buckets=200)
            except Exception:  # noqa: BLE001
                peaks = []
            self.after(0, lambda: self._apply_peaks(peaks))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_peaks(self, peaks: list[float]) -> None:
        project = self.app.project
        if project is not None:
            project.audio_peaks = peaks
        self._waveform.set_peaks(peaks)
        self._preview.redraw()

    # -- export -------------------------------------------------------------

    def _export(self) -> None:
        project = self.app.project
        if project is None:
            return
        if not project.images and not project.movie.audio:
            dialogs.warn("Stillpoint", "Add at least one image or some audio first.", parent=self)
            return
        problems = project.validate()
        if problems:
            dialogs.error("Stillpoint", "\n".join(problems), parent=self)
            return
        out_path = render.render_output_path(project)
        self._export.configure(state="disabled", text="Rendering…")
        self._progress.configure(value=0)
        self._worker = RenderWorker(project, out_path)
        self._worker.start()

    def _poll_render(self) -> None:
        if self._worker:
            status = self._worker.poll()
            while status:
                if status.state == "progress":
                    self._progress.configure(value=status.value * 100)
                elif status.state == "done":
                    self._finish_render(status.value)
                elif status.state == "error":
                    self._worker = None
                    self._export.configure(state="normal", text="Export…")
                    self._progress.configure(value=0)
                    dialogs.error("Stillpoint", f"Render failed:\n{status.value}", parent=self)
                status = self._worker.poll() if self._worker else None
        self.after(150, self._poll_render)

    def _finish_render(self, out: str) -> None:
        self._worker = None
        self._export.configure(state="normal", text="Export…")
        self._progress.configure(value=100)
        dialogs.info("Stillpoint", f"Done. Your film is ready:\n{out}", parent=self)

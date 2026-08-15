"""Export the finished Stillpoint mix as video or audio.

The module keeps the format choices deliberately small.  Encoding is done in
a worker thread, while all Tk updates are marshalled back to the root window.
"""

from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Literal

from PIL import Image, ImageDraw, ImageFont

from .. import media, mix, names, render

MAX_AUDIO_BYTES = 16 * 1024 * 1024
MIN_AUDIO_BITRATE = 96
MAX_AUDIO_BITRATE = 256
WARNING_DURATION_SECONDS = MAX_AUDIO_BYTES * 8 / (MIN_AUDIO_BITRATE * 1000)
BG_COLOR = "#12121A"


@dataclass(frozen=True)
class VideoSettings:
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    crf: int = 20
    preset: str = "medium"


@dataclass
class AudioSettings:
    container: str = "m4a"
    codec: str = "aac"
    channels: int = 2
    bitrate_kbps: int = 192
    sample_rate: int = 44100


@dataclass
class ExportJob:
    format: Literal["video", "audio"]
    target_path: Path
    video_settings: VideoSettings | None = None
    audio_settings: AudioSettings | None = None
    progress: float = 0.0
    status: Literal["pending", "running", "completed", "cancelled", "failed"] = "pending"
    error_message: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


def compute_audio_bitrate_kbps(duration_seconds: float) -> int:
    if duration_seconds <= 0:
        return MAX_AUDIO_BITRATE
    raw = int((MAX_AUDIO_BYTES * 8) / duration_seconds / 1000)
    return max(MIN_AUDIO_BITRATE, min(MAX_AUDIO_BITRATE, raw))


def check_audio_duration_warning(duration_seconds: float) -> tuple[bool, str]:
    warning = (
        "This meditation is too long for WhatsApp's 16 MB limit at good quality. "
        "The file would be larger than 16 MB or sound noticeably degraded. "
        "You can still export, but quality will be reduced."
    )
    return duration_seconds > WARNING_DURATION_SECONDS, warning


def generate_placeholder_image(project, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1920, 1080), BG_COLOR)
    draw = ImageDraw.Draw(image)
    title = str(getattr(project, "title", "Stillpoint"))
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), title, font=font)
    draw.text(((1920 - (box[2] - box[0])) / 2, (1080 - (box[3] - box[1])) / 2), title, fill="white", font=font)
    image.save(out_path, format="PNG")
    return out_path


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, FileNotFoundError) or "ffmpeg" in text.lower() and "start" in text.lower():
        return "Export needs the video tool. Please reinstall Stillpoint."
    if isinstance(exc, PermissionError):
        return "Can't write to that folder. Choose a different location."
    if "no space" in text.lower() or "disk full" in text.lower():
        return "Not enough space on disk. Free up space and try again."
    return text or "Export failed. Please try again."


class ExportWorker(threading.Thread):
    def __init__(self, job: ExportJob, project, progress_cb: Callable[[float], None], done_cb: Callable[[bool, str | None], None]):
        super().__init__(name="stillpoint-export", daemon=True)
        self.job = job
        self.project = project
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.job.cancel_event.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()

    def run(self) -> None:
        self.job.status = "running"
        temp_path = self.job.target_path.with_name(self.job.target_path.name + ".part")
        try:
            temp_path.unlink(missing_ok=True)
            if self.job.format == "video":
                render.render_with_progress(self.project, temp_path, self._progress)
            else:
                self._run_audio(temp_path)
            if self.job.cancel_event.is_set():
                raise InterruptedError
            self.job.target_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, self.job.target_path)
            self.job.status = "completed"
            self.job.progress = 1.0
            self.done_cb(True, None)
        except InterruptedError:
            self.job.status = "cancelled"
            temp_path.unlink(missing_ok=True)
            self.done_cb(False, "Export cancelled.")
        except Exception as exc:  # noqa: BLE001 - reported in the completion dialog
            self.job.status = "failed"
            self.job.error_message = _friendly_error(exc)
            temp_path.unlink(missing_ok=True)
            self.done_cb(False, self.job.error_message)

    def _progress(self, fraction: float) -> None:
        self.job.progress = max(0.0, min(1.0, fraction))
        if self.job.cancel_event.is_set():
            self.cancel()
        self.progress_cb(self.job.progress)

    def _run_audio(self, temp_path: Path) -> None:
        settings = self.job.audio_settings or AudioSettings()
        total = mix.timeline_duration(self.project)
        plan = mix.plan_audio(self.project, total)
        if not plan.has_audio:
            raise RuntimeError("There is no audio to export yet.")
        cmd = [str(media.ffmpeg_path()), "-y", "-v", "error", *plan.inputs,
               "-filter_complex", ";".join(plan.chains), "-map", "[aout]",
               "-c:a", settings.codec, "-b:a", f"{settings.bitrate_kbps}k",
               "-ac", str(settings.channels), "-ar", str(settings.sample_rate),
               "-f", "ipad", "-progress", "pipe:1", "-nostats", str(temp_path)]
        self._process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert self._process.stdout is not None
        for raw in self._process.stdout:
            if self.job.cancel_event.is_set():
                self.cancel()
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("out_time_us="):
                try:
                    self._progress(float(line.split("=", 1)[1]) / 1_000_000 / total)
                except (ValueError, ZeroDivisionError):
                    pass
        stderr = self._process.stderr.read().decode("utf-8", errors="replace") if self._process.stderr else ""
        code = self._process.wait()
        self._process = None
        if self.job.cancel_event.is_set():
            raise InterruptedError
        if code != 0:
            raise RuntimeError(stderr[-800:] or "ffmpeg failed")
        self._progress(1.0)


class ExportProgressDialog:
    def __init__(self, parent, job: ExportJob, worker: ExportWorker):
        self.job, self.worker = job, worker
        self.window = tk.Toplevel(parent)
        self.window.title("Exporting Video" if job.format == "video" else "Exporting Audio")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.label = tk.Label(self.window, text="Starting export...")
        self.label.pack(padx=24, pady=(20, 8))
        self.bar = ttk.Progressbar(self.window, mode="determinate", maximum=100, length=320)
        self.bar.pack(padx=24, pady=8)
        self.button = tk.Button(self.window, text="Cancel", command=self.cancel)
        self.button.pack(pady=(8, 20))
        worker.progress_cb = self.update
        worker.done_cb = self.done
        worker.start()

    def update(self, fraction: float) -> None:
        try:
            self.window.after(0, lambda: (self.bar.configure(value=fraction * 100), self.label.configure(text=f"Exporting... {fraction:.0%}")))
        except tk.TclError:
            pass

    def cancel(self) -> None:
        self.worker.cancel()

    def done(self, success: bool, error: str | None) -> None:
        try:
            self.window.after(0, lambda: self._finish(success, error))
        except tk.TclError:
            pass

    def _finish(self, success: bool, error: str | None) -> None:
        self.window.grab_release()
        self.window.destroy()
        if success:
            show_export_complete(self.window.master, self.job.target_path, self.job.format)
        elif error and error != "Export cancelled.":
            messagebox.showerror("Export", error, parent=self.window.master)


def show_export_complete(parent, file_path: Path, format: str) -> None:
    window = tk.Toplevel(parent)
    window.title("Export complete")
    tk.Label(window, text="Your video is ready" if format == "video" else "Your audio is ready").pack(padx=24, pady=(20, 8))
    value = tk.Entry(window, width=70)
    value.insert(0, str(file_path))
    value.configure(state="readonly")
    value.pack(padx=24, pady=8)
    tk.Button(window, text="Open Folder", command=lambda: os.startfile(file_path.parent)).pack(side="left", padx=(24, 8), pady=20)
    tk.Button(window, text="OK", command=window.destroy).pack(side="right", padx=(8, 24), pady=20)


def show_export_dialog(app, project) -> None:
    if project is None:
        return
    parent = app.root
    window = tk.Toplevel(parent)
    window.title("Export")
    window.transient(parent)
    window.grab_set()
    choice = tk.StringVar(value="video")
    tk.Label(window, text="Choose export format").pack(anchor="w", padx=20, pady=(18, 8))
    tk.Radiobutton(window, text="Video (YouTube) — 1080p, 30 fps", variable=choice, value="video").pack(anchor="w", padx=20)
    duration = mix.timeline_duration(project)
    bitrate = compute_audio_bitrate_kbps(duration)
    tk.Radiobutton(window, text=f"Audio only (WhatsApp) — AAC stereo, ~{bitrate} kbps", variable=choice, value="audio").pack(anchor="w", padx=20)
    location = tk.StringVar(value=str((project.directory or Path.cwd()) / "renders"))
    row = tk.Frame(window); row.pack(fill="x", padx=20, pady=12)
    tk.Entry(row, textvariable=location).pack(side="left", fill="x", expand=True)
    tk.Button(row, text="Browse...", command=lambda: location.set(filedialog.askdirectory(parent=window) or location.get())).pack(side="right", padx=(8, 0))

    def confirm() -> None:
        fmt = choice.get()
        if fmt == "audio":
            warn, message = check_audio_duration_warning(duration)
            if warn and not messagebox.askokcancel("Long meditation", message, parent=window):
                return
        directory = Path(location.get()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stem = names.sanitize_filename(project.title)
        target = directory / (f"{stem} - {project.movie.ratio.replace(':', 'x')}.mp4" if fmt == "video" else f"{stem}.m4a")
        job = ExportJob(fmt, target, VideoSettings() if fmt == "video" else None, AudioSettings(bitrate_kbps=bitrate) if fmt == "audio" else None)
        window.grab_release(); window.destroy()
        ExportProgressDialog(parent, job, ExportWorker(job, project, lambda _f: None, lambda _s, _e: None))

    tk.Button(window, text="Export", command=confirm).pack(side="right", padx=(8, 20), pady=20)
    tk.Button(window, text="Cancel", command=window.destroy).pack(side="right", pady=20)


__all__ = ["VideoSettings", "AudioSettings", "ExportJob", "ExportWorker", "ExportProgressDialog", "compute_audio_bitrate_kbps", "check_audio_duration_warning", "generate_placeholder_image", "show_export_dialog", "show_export_complete"]

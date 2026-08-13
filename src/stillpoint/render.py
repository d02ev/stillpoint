"""Rendering: turn a project's timeline into an mp4 via ffmpeg.

The movie is: each still image shown for its own duration with optional
crossfades between consecutive images, over a fixed canvas, with an optional
ambient audio track (trimmed to the movie length, faded in/out).

The ffmpeg command is built as data (a list of args) so the GUI can preview or
customise it, and a `render()` driver runs it while parsing `-progress` output
into a 0..1 progress callback.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import model as model_mod
from . import names, paths
from .media import ffmpeg_path

FPS = 30

CANVAS_SIZES: dict[str, tuple[int, int]] = {
    model_mod.RATIO_WIDE: (1280, 720),
    model_mod.RATIO_SQUARE: (1080, 1080),
    model_mod.RATIO_VERTICAL: (720, 1280),
}

BG_COLOR = "0x12121A"


class RenderError(RuntimeError):
    pass


@dataclass
class RenderSpec:
    """Everything ffmpeg needs, precomputed from a project."""

    inputs: list[str]  # ['-loop','1','-framerate','30','-t','5.0','-i','img.jpg', ...]
    filter_complex: str
    has_audio: bool
    total: float  # seconds of the finished film
    out_path: Path


def _scale_pad(v: str, width: int, height: int) -> str:
    """Center-crop (fill) each input to the canvas so xfade matches sizes."""
    return (
        f"{v}scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def timeline_duration(project: model_mod.Project) -> float:
    """Seconds of finished film: images overlap by the crossfade."""
    movie = project.movie
    if not project.images:
        return movie.duration
    raw = sum(item.duration for item in project.images)
    overlap = movie.crossfade * (len(project.images) - 1)
    return max(1.0, raw - overlap)


def build_spec(project: model_mod.Project, out_path: Path) -> RenderSpec:
    """Build the ffmpeg command pieces for a project."""
    movie = project.movie
    width, height = CANVAS_SIZES.get(movie.ratio, CANVAS_SIZES[model_mod.RATIO_WIDE])
    total = timeline_duration(project)
    inputs: list[str] = []
    chains: list[str] = []
    images = project.images

    if not images:
        chains.append(f"color=c={BG_COLOR}:s={width}x{height}:d={total}:r={FPS}[vbase]")
    else:
        for index, item in enumerate(images):
            length = item.duration + movie.crossfade + 0.25  # headroom for the fade
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", str(project.media_file(item))]
            src = f"[{index}:v]"
            prep = _scale_pad(src, width, height)
            chains.append(f"{prep},trim=duration={length:.3f},setpts=PTS-STARTPTS[v{index}]")

        if movie.crossfade <= 0:
            join = "".join(f"[v{i}]" for i in range(len(images)))
            chains.append(f"{join}concat=n={len(images)}:v=1:a=0[vjoin]")
        else:
            offset = 0.0
            for index in range(1, len(images)):
                offset = offset + images[index - 1].duration - movie.crossfade
                left = "v0" if index == 1 else f"x{index - 1}"
                out_label = "vjoin" if index == len(images) - 1 else f"x{index}"
                chains.append(
                    f"[{left}][v{index}]"
                    f"xfade=transition=fade:duration={movie.crossfade:.3f}:offset={offset:.3f}[{out_label}]"
                )

    has_audio = movie.audio is not None
    if has_audio and movie.audio is not None:
        audio_file = project.media_file(movie.audio)
        inputs += ["-i", str(audio_file)]
        fade_in = max(0.0, movie.audio.fade_in)
        fade_out = max(0.0, movie.audio.fade_out)
        fade_out = min(fade_out, total * 0.5)
        volume = max(0.0, min(1.0, movie.audio.volume))
        chain = (
            f"[{len(images)}:a]"
            f"atrim=start={movie.audio.in_point:.3f}:end={movie.audio.in_point + total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.3f},apad,atrim=0:{total:.3f}"
        )
        if fade_in > 0:
            chain += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out > 0:
            chain += f",afade=t=out:st={total - fade_out:.3f}:d={fade_out:.3f}"
        chain += "[aout]"
        chains.append(chain)

    if images:
        chains.append(f"[vjoin]trim=duration={total:.3f},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[vout]")
    else:
        chains.append(f"[vbase]fps={FPS},format=yuv420p[vout]")

    filter_complex = ";".join(chains)
    return RenderSpec(
        inputs=inputs,
        filter_complex=filter_complex,
        has_audio=has_audio,
        total=total,
        out_path=out_path,
    )


def build_command(spec: RenderSpec) -> list[str]:
    """Full ffmpeg command line for a spec."""
    cmd = [
        str(ffmpeg_path()),
        "-y",
        "-v", "error",
        *spec.inputs,
        "-filter_complex", spec.filter_complex,
        "-map", "[vout]",
    ]
    if spec.has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(spec.out_path),
    ]
    return cmd


def render(project: model_mod.Project, out_path: Path, progress_cb=None) -> Path:
    """Render a project to out_path; calls progress_cb(fraction) as it goes."""
    spec = build_spec(project, out_path)
    cmd = build_command(spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
        )
    except OSError as exc:
        raise RenderError(f"could not start ffmpeg: {exc}") from exc
    _raise_on_failure(process, spec)
    if progress_cb:
        progress_cb(1.0)
    return out_path


def render_with_progress(project: model_mod.Project, out_path: Path, progress_cb=None) -> Path:
    """Like render(), but streams ffmpeg's -progress output to progress_cb."""
    spec = build_spec(project, out_path)
    cmd = build_command(spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    seen: dict[str, str] = {}
    last_fraction = 0.0

    def _update(key: str, value: str) -> None:
        nonlocal last_fraction
        seen[key] = value
        if key == "out_time_us" and spec.total > 0:
            try:
                elapsed = int(value) / 1_000_000
                fraction = min(1.0, elapsed / spec.total)
            except ValueError:
                return
            if progress_cb and fraction - last_fraction >= 0.005:
                last_fraction = fraction
                progress_cb(fraction)

    for line in process.stdout:  # -progress pipe:1 writes key=value to stdout
        text = line.decode("utf-8", errors="replace").strip()
        if "=" in text:
            key, _, value = text.partition("=")
            _update(key, value)
    err = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()
    if process.returncode != 0:
        raise RenderError(f"ffmpeg exited with a non-zero status:\n{err[-800:]}")
    if progress_cb:
        progress_cb(1.0)
    return out_path


def _raise_on_failure(process: subprocess.CompletedProcess, spec: RenderSpec) -> None:
    if process.returncode == 0:
        return
    detail = process.stderr.decode("utf-8", errors="replace")[-800:] if process.stderr else ""
    raise RenderError(f"ffmpeg render failed:\n{detail}")


def render_output_path(project: model_mod.Project) -> Path:
    """A fresh path inside the project's renders/ folder."""
    renders = project.directory / "renders" if project.directory else paths.default_projects_dir() / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    base = f"{names.sanitize_filename(project.title)} - {project.movie.ratio.replace(':', 'x')}"
    return renders / names.unique_filename(renders, base, ".mp4")

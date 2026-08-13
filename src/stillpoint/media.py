"""Media handling: still-image processing (via Pillow) and audio probing
(duration + waveform) via ffmpeg/ffprobe.

ffmpeg is located at runtime: STILLPOINT_FFMPEG_DIR env var first, then PATH,
then the common WinGet install location. All audio helpers raise
FfmpegNotFoundError when the tools aren't available.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from . import paths

JPEG_QUALITY = 88
WAVEFORM_SAMPLE_RATE = 8000


class FfmpegNotFoundError(RuntimeError):
    pass


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    override = os.environ.get("STILLPOINT_FFMPEG_DIR")
    if override:
        dirs.append(Path(override))
    # Common WinGet install location (ffmpeg-9.0-full_build layout).
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "Microsoft/WinGet/Packages"
        for folder in sorted(root.glob("Gyan.FFmpeg*"), reverse=True):
            dirs.append(folder / "bin")
            dirs.extend(sorted(folder.glob("*/bin")))
    return dirs


def find_ffmpeg(exe: str = "ffmpeg") -> Path:
    """Locate an ffmpeg-family binary, preferring ffmpeg.exe on Windows."""
    candidates: list[Path] = []
    for d in _candidate_dirs():
        if d.is_dir():
            candidates.append(d / (exe + ".exe"))
            candidates.append(d / exe)
    for cand in candidates:
        if cand.is_file():
            return cand
    found = shutil.which(exe)
    if found:
        return Path(found)
    raise FfmpegNotFoundError(f"{exe} not found; install ffmpeg or set STILLPOINT_FFMPEG_DIR")


@lru_cache(maxsize=None)
def ffmpeg_path() -> Path:
    return find_ffmpeg("ffmpeg")


@lru_cache(maxsize=None)
def ffprobe_path() -> Path:
    return find_ffmpeg("ffprobe")


def _run(cmd: list[str]) -> bytes:
    try:
        return subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            check=True,
            timeout=120,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg command failed: {' '.join(cmd)}\n{detail}") from exc


# -- probing -----------------------------------------------------------------


def probe_duration(path: Path) -> float:
    """Duration of a media file in seconds."""
    output = _run([
        ffprobe_path(),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]).decode("utf-8").strip()
    try:
        return float(output)
    except ValueError as exc:
        raise RuntimeError(f"could not parse duration from ffprobe: {output!r}") from exc


def audio_duration(path: Path) -> float:
    """Duration of an audio file in seconds."""
    return probe_duration(path)


def waveform_peaks(path: Path, buckets: int = 200) -> list[float]:
    """Peak amplitude per bucket (0..1) for drawing a waveform.

    Decodes the whole clip to low-rate mono f32 and computes the max absolute
    sample per bucket, so silence and loudness are both visible.
    """
    if buckets < 1:
        raise ValueError("buckets must be at least 1")
    raw = _run([
        ffmpeg_path(),
        "-v", "error",
        "-i", str(path),
        "-f", "f32le",
        "-ac", "1",
        "-ar", str(WAVEFORM_SAMPLE_RATE),
        "-",
    ])
    samples = len(raw) // 4
    if samples == 0:
        return [0.0] * buckets
    values = [abs(struct.unpack("<f", raw[i : i + 4])[0]) for i in range(0, samples * 4, 4)]
    peaks = [0.0] * buckets
    per_bucket = max(1, samples // buckets)
    for b in range(buckets):
        start = b * per_bucket
        end = start + per_bucket
        if start >= len(values):
            break
        peaks[b] = max(values[start:end])
    return [min(1.0, p) for p in peaks]


# -- still images -------------------------------------------------------------


def open_image(path: Path) -> Image.Image:
    """Open an image and convert it to RGB."""
    with Image.open(path) as img:
        return img.convert("RGB")


def cover_transform(image: Image.Image, width: int, height: int) -> Image.Image:
    """Crop to fill the given canvas (centre), then resize; keeps no bars."""
    return ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)


def save_image_jpeg(image: Image.Image, out_path: Path, quality: int = JPEG_QUALITY) -> Path:
    """Save an RGB image as a high-quality JPEG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "JPEG", quality=quality)
    return out_path


def import_image(source: Path, out_path: Path, width: int, height: int) -> Path:
    """Copy a source image into a project's media folder as a cover-cropped JPEG."""
    image = open_image(source)
    fitted = cover_transform(image, width, height)
    return save_image_jpeg(fitted, out_path)


# -- audio conversion ------------------------------------------------------------


def convert_to_m4a(src: Path, out: Path) -> None:
    """Convert any audio file to the standard .m4a/AAC (≥192 kbps).

    Writes to a temp file in the destination directory and moves it into place
    with ``os.replace`` so an interrupted conversion never leaves a partial file
    (FR-008, Constitution IV).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=out.name + ".", suffix=".tmp", dir=out.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        cmd = [
            str(ffmpeg_path()),
            "-y", "-v", "error",
            "-i", str(src),
            "-vn",
            "-c:a", "aac",
            "-b:a", "192k",
            str(tmp),
        ]
        subprocess.run([str(c) for c in cmd], capture_output=True, check=True, timeout=600)
        os.replace(tmp, out)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"audio conversion failed:\n{detail}") from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

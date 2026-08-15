"""Project model: a project is a directory with a project.json file.

A project contains source media (still images, audio) in `media/`, an optional
`renders/` folder for exported videos, and a `project.json` that describes the
movie. This module owns reading and writing that file, and packing a project
into a zip for sharing.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import names

PROJECT_FILENAME = "project.json"
PROJECT_VERSION = 1
FRIENDLY_NAME = "Stillpoint Project"
PROJECT_NAME_RE = re.compile(r"^[\w .\-()&']+$", re.UNICODE)

# Aspect ratios users can pick; stored as a plain string in project.json.
RATIO_WIDE = "16:9"
RATIO_SQUARE = "1:1"
RATIO_VERTICAL = "9:16"
RATIO_CHOICES = (RATIO_WIDE, RATIO_SQUARE, RATIO_VERTICAL)

FPS = 30

#: Longest fade a channel may use (seconds) — the shared cap for the slider
#: and the model clamp (research Decision 3).
FADE_MAX_SECONDS = 10.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _bounded_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _bounded_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass
class MediaItem:
    """One still image or audio clip in a movie."""

    kind: str  # 'image' or 'audio'
    filename: str  # name of the file inside media/
    duration: float = 5.0  # seconds; audio can be trimmed to a portion
    in_point: float = 0.0  # for audio: offset into the source clip (s)
    volume: float = 1.0  # 0..1
    echo: float = 0.0  # echo strength, 0..1; 0.0 = off (006; the one new field)
    fade_in: float = 0.0  # seconds
    fade_out: float = 0.0  # seconds

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "duration": round(self.duration, 3),
            "in_point": round(self.in_point, 3),
            "volume": round(self.volume, 3),
            "echo": round(self.echo, 3),
            "fade_in": round(self.fade_in, 3),
            "fade_out": round(self.fade_out, 3),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MediaItem":
        known = {"kind", "filename", "duration", "in_point", "volume", "echo", "fade_in", "fade_out"}
        if not isinstance(data, dict):
            raise ValueError("media item is not an object")
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"media item has unknown keys: {sorted(unknown)}")
        kind = data.get("kind")
        if kind not in ("image", "audio"):
            raise ValueError("media item kind must be 'image' or 'audio'")
        return cls(
            kind=kind,
            filename=str(data.get("filename", "")),
            duration=float(data.get("duration", 5.0)),
            in_point=float(data.get("in_point", 0.0)),
            volume=float(data.get("volume", 1.0)),
            echo=float(data.get("echo", 0.0)),
            fade_in=float(data.get("fade_in", 0.0)),
            fade_out=float(data.get("fade_out", 0.0)),
        )


@dataclass
class Movie:
    """A movie: ordered images plus the two audio roles (music + voice)."""

    duration: float = 60.0
    ratio: str = RATIO_WIDE
    crossfade: float = 0.0  # seconds, applied between consecutive images
    audio: MediaItem | None = None  # background music role
    voice: MediaItem | None = None  # voice role

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 3),
            "ratio": self.ratio,
            "crossfade": round(self.crossfade, 3),
            "audio": self.audio.to_dict() if self.audio else None,
            "voice": self.voice.to_dict() if self.voice else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Movie":
        ratio = data.get("ratio", RATIO_WIDE)
        if ratio not in RATIO_CHOICES:
            raise ValueError(f"unsupported ratio: {ratio}")
        audio_data = data.get("audio")
        voice_data = data.get("voice")
        audio = MediaItem.from_dict(audio_data) if audio_data else None
        voice = MediaItem.from_dict(voice_data) if voice_data else None
        return cls(
            duration=float(data.get("duration", 60.0)),
            ratio=ratio,
            crossfade=float(data.get("crossfade", 0.0)),
            audio=audio,
            voice=voice,
        )


@dataclass
class Project:
    """The in-memory view of a project, mirroring project.json."""

    title: str = "Untitled"
    created: str = ""  # ISO timestamp
    ratio: str = RATIO_WIDE
    image_duration: float = 5.0  # default duration for newly added images
    movie: Movie = field(default_factory=Movie)
    # Loose ordering; images only. Represented as a list of MediaItem in dict form.
    images: list[MediaItem] = field(default_factory=list)
    directory: Path | None = None  # where the project lives (not persisted)
    audio_peaks: list[float] = field(default_factory=list, repr=False)  # transient, for the UI

    # -- validation ----------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of human-readable problems with this project."""
        problems: list[str] = []
        if not self.title.strip():
            problems.append("The project title is empty.")
        elif len(self.title) > 100:
            problems.append("The project title is longer than 100 characters.")
        elif not PROJECT_NAME_RE.match(self.title):
            problems.append("The project title contains characters that can't be used in a filename.")
        if self.ratio not in RATIO_CHOICES:
            problems.append(f"The ratio '{self.ratio}' is not supported.")
        if self.movie.duration < 1:
            problems.append("The movie must be at least 1 second long.")
        if self.movie.crossfade < 0 or self.movie.crossfade > self.movie.duration / 2:
            problems.append("The crossfade is too long for the movie duration.")
        if len(self.images) > 300:
            problems.append("A movie can contain at most 300 images.")
        for index, item in enumerate(self.images):
            if item.duration < 0.2:
                problems.append(f"Image {index + 1} is shorter than 0.2 seconds.")
        return problems

    def _validate_raising(self) -> None:
        problems = self.validate()
        if problems:
            raise ValueError("; ".join(problems))

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": PROJECT_VERSION,
            "title": self.title,
            "created": self.created,
            "ratio": self.ratio,
            "imageDuration": self.image_duration,
            "movie": self.movie.to_dict(),
            "images": [item.to_dict() for item in self.images],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        movie_data = data.get("movie", {})
        images_data = data.get("images", [])
        if not isinstance(images_data, list):
            raise ValueError("images is not a list")
        movie = Movie.from_dict(movie_data)
        images = [MediaItem.from_dict(item) for item in images_data]
        return cls(
            title=str(data.get("title", "Untitled")),
            created=str(data.get("created", "")),
            ratio=movie.ratio,
            image_duration=float(data.get("imageDuration", 5.0)),
            movie=movie,
            images=images,
        )

    # -- disk ------------------------------------------------------------

    @property
    def project_file(self) -> Path:
        if self.directory is None:
            raise RuntimeError("project has no directory")
        return self.directory / PROJECT_FILENAME

    def save(self) -> None:
        """Write project.json into the project directory (no media)."""
        from . import io

        self._validate_raising()
        if self.directory is None:
            raise RuntimeError("cannot save: no directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "media").mkdir(exist_ok=True)
        (self.directory / "renders").mkdir(exist_ok=True)
        io.atomic_write_json(self.project_file, self.to_dict())

    @classmethod
    def load(cls, directory: Path) -> "Project":
        from . import io

        project_file = directory / PROJECT_FILENAME
        data = io.read_json(project_file)
        if data is None:
            raise FileNotFoundError(f"no project.json in {directory}")
        project = cls.from_dict(data)
        project.directory = directory
        return project

    # -- media ------------------------------------------------------------

    def media_dir(self) -> Path:
        if self.directory is None:
            raise RuntimeError("project has no directory")
        return self.directory / "media"

    def media_file(self, item: MediaItem) -> Path:
        return self.media_dir() / item.filename

    def add_image(self, source: Path) -> MediaItem:
        """Copy an image into media/ and append it to the movie."""
        import shutil

        self.media_dir().mkdir(parents=True, exist_ok=True)
        new_name = names.unique_filename(self.media_dir(), source.stem, source.suffix.lower())
        destination = self.media_dir() / new_name
        shutil.copy2(source, destination)
        item = MediaItem(kind="image", filename=new_name, duration=self.image_duration)
        self.images.append(item)
        return item

    def set_background_image(self, filename: str) -> MediaItem:
        """Set the project's single background image.

        Replaces ``self.images`` with exactly one ``MediaItem(kind="image",
        filename=..., duration=movie.duration)``, then saves ``project.json``
        immediately and atomically via :meth:`save` (reconciliation R2/R3). The
        still background spans the whole film (``movie.duration``), so the
        timeline — and the background music trimmed to it — lasts the full
        meditation instead of collapsing to the short default picture length.
        No new schema fields — the background reuses the existing ``images``
        list (Constitution VIII). Raises ``ValueError`` if the file is not
        present in ``media/`` (an internal disk race, surfaced as the plain
        "try again" message). A no-op (no save) when the same filename is
        already the only entry (R3).
        """
        source = self.media_dir() / filename
        if not source.is_file():
            raise ValueError(f"no image file named {filename!r} in this project")
        if len(self.images) == 1 and self.images[0].kind == "image" and self.images[0].filename == filename:
            return self.images[0]
        item = MediaItem(kind="image", filename=filename, duration=self.movie.duration)
        self.images = [item]
        self.save()
        return item

    def set_background_music(self, filename: str) -> MediaItem:
        """Import a downloaded track into channel 1 (background music).

        Assigns a fresh ``MediaItem`` to ``movie.audio`` (replacing any previous
        music — swap, FR-016), then saves ``project.json`` immediately and
        atomically via :meth:`save` (reconciliation R2). No new schema fields.
        Raises ``ValueError`` if the file is not present in ``media/`` (an
        internal disk race, surfaced as a plain "try again" message).
        """
        source = self.media_dir() / filename
        if not source.is_file():
            raise ValueError(f"no audio file named {filename!r} in this project")
        item = MediaItem(kind="audio", filename=filename)
        self.movie.audio = item
        self.save()
        return item

    def set_voice(self, filename: str) -> MediaItem:
        """Import a local voice file into channel 2 (voice).

        Mirrors :meth:`set_background_music`: assigns a fresh ``MediaItem`` to
        ``movie.voice``, then saves ``project.json`` immediately and atomically
        (reconciliation R2). No new schema fields (Constitution VIII). Raises
        ``ValueError`` if the file is not present in ``media/`` (an internal
        disk race, surfaced as the plain "try again" message).
        """
        source = self.media_dir() / filename
        if not source.is_file():
            raise ValueError(f"no audio file named {filename!r} in this project")
        item = MediaItem(kind="audio", filename=filename)
        self.movie.voice = item
        self.save()
        return item

    def set_channel_setting(self, role: str, setting: str, value: float) -> None:
        """Set a channel's edit-state scalar and persist it atomically.

        ``role`` ∈ ``"music"`` | ``"voice"``; ``setting`` ∈ ``{"volume",
        "echo", "fade_in", "fade_out"}``. Clamps before writing: ``volume``/
        ``echo`` → 0..1; ``fade_in``/``fade_out`` → 0..``FADE_MAX_SECONDS``.
        Writes ``movie.<role>.<setting>`` and saves immediately and atomically
        via :meth:`save` (reconciliation R2, Constitution IV) — the source
        audio file is never touched (FR-010). Raises ``ValueError`` for an
        unknown role/setting or an unrecorded channel. The change is heard on
        the next play-from-stop (signature-driven re-bake) and is identical in
        preview and export.
        """
        item = {"music": self.movie.audio, "voice": self.movie.voice}.get(role)
        if item is None:
            raise ValueError(f"no {role} channel is recorded")
        if setting not in ("volume", "echo", "fade_in", "fade_out"):
            raise ValueError(f"unknown channel setting: {setting!r}")
        if setting in ("volume", "echo"):
            setattr(item, setting, _clamp(value, 0.0, 1.0))
        else:
            setattr(item, setting, _clamp(value, 0.0, FADE_MAX_SECONDS))
        self.save()

    def set_channel_volume(self, role: str, volume: float) -> None:
        """Compatibility alias for :meth:`set_channel_setting` (volume only)."""
        self.set_channel_setting(role, "volume", volume)

    # -- export / share -----------------------------------------------------

    def make_share_archive(self, zip_path: Path) -> None:
        """Pack the project into a zip (project.json + media, no renders)."""
        if self.directory is None:
            raise RuntimeError("cannot share a project with no directory")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(self.project_file, arcname=PROJECT_FILENAME)
            for item in self.images:
                source = self.media_file(item)
                if source.exists():
                    zf.write(source, arcname=f"media/{item.filename}")


def new_project(title: str, directory: Path, created: str, ratio: str = RATIO_WIDE) -> Project:
    """Create and persist a fresh project, returning it."""
    project = Project(
        title=title,
        created=created,
        ratio=ratio,
        image_duration=5.0,
        directory=directory,
    )
    project.movie.ratio = ratio
    project.save()
    return project

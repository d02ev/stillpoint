"""Download orchestration: transient job state, the track list, and the
one-download-at-a-time guard.

Pure, display-free logic (no Tk, no yt-dlp import at module top). The GUI wraps
:func:`download_track` in a worker thread exactly like the existing render
worker; every rule (progress narrative, stop cleanup, atomic storage, error
buckets) is unit-testable headlessly with a fake fetcher.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import media, names, youtube
from .youtube import (
    BAD_LINK_MESSAGE,
    DownloadError,
    DownloadStopped,
    classify_error,
)

# -- canonical plain-language strings (contracts/download-ui.md) ---------------

FINDING = "Finding the music"
CONVERTING = "Converting"
DONE = "Done."
STOPPED = "Stopped."
BUSY = "A download is already running. Stop it first."

TERMINAL_STATES = ("done", "stopped", "error")


@dataclass
class DownloadEvent:
    """One job event the GUI renders verbatim.

    ``state`` is ``finding | downloading | converting | done | stopped | error``;
    ``value`` is a 0..1 fraction for ``downloading``, else 0; ``detail`` is the
    plain-language line to show.
    """

    state: str
    value: float = 0.0
    detail: str = ""


class DownloadManager:
    """Guards the one-download-at-a-time rule (FR-022). Thread-safe."""

    def __init__(self) -> None:
        self._active = False
        self._lock = threading.Lock()

    def try_begin(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def end(self) -> None:
        with self._lock:
            self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active


_manager = DownloadManager()


def list_downloaded_tracks(project) -> list[str]:
    """Audio files in the project's media/ folder, newest first.

    Derived from the folder itself (FR-009/FR-010): a track appears the moment
    its file is stored and disappears if the file is gone.
    """
    media_dir = project.media_dir()
    if not media_dir.is_dir():
        return []
    tracks = [
        p
        for p in media_dir.iterdir()
        if p.is_file() and p.suffix.lower() in youtube.AUDIO_EXTENSIONS
    ]
    tracks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in tracks]


def download_track(project, url, *, progress=None, should_stop=None, fetch=None) -> str:
    """Download audio-only music for ``url`` into the project's media folder.

    Emits :class:`DownloadEvent` objects through ``progress`` as the job
    advances, returns the stored filename on success, and raises
    :class:`DownloadError` (bad_link/no_connection/other) on failure or
    :class:`DownloadStopped` when ``should_stop()`` turns true. The final file
    appears in ``media/`` only when fully stored (atomic, FR-008).
    """
    emit = progress or (lambda _event: None)
    stop = should_stop or (lambda: False)
    fetch = fetch or youtube.fetch_audio

    if not _manager.try_begin():
        emit(DownloadEvent("error", 0.0, BUSY))
        raise DownloadError("other", BUSY)
    try:
        return _run(project, url, emit, stop, fetch)
    finally:
        _manager.end()


def _run(project, url, emit, stop, fetch) -> str:
    media_dir = project.media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".stillpoint-dl-", dir=media_dir))
    try:
        if not youtube.is_video_url(url):
            _fail(emit, "bad_link", youtube.BAD_LINK_MESSAGE)
        emit(DownloadEvent("finding", 0.0, FINDING))
        title, source = fetch(url, temp_dir, on_progress=_on_progress(emit), should_stop=stop)
        _raise_if_stopped(stop)
        emit(DownloadEvent("converting", 0.0, CONVERTING))
        staging = temp_dir / ("converted" + youtube.STANDARD_AUDIO_EXT)
        if source.suffix.lower() == youtube.STANDARD_AUDIO_EXT:
            os.replace(source, staging)
        else:
            media.convert_to_m4a(source, staging)
        _raise_if_stopped(stop)
        stem = names.sanitize_filename(title)
        filename = names.unique_filename(media_dir, stem, youtube.STANDARD_AUDIO_EXT)
        os.replace(staging, media_dir / filename)
        emit(DownloadEvent("done", 0.0, DONE))
        return filename
    except DownloadStopped:
        emit(DownloadEvent("stopped", 0.0, STOPPED))
        raise
    except DownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = classify_error(exc)
        emit(DownloadEvent("error", 0.0, message))
        raise DownloadError(kind, message) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _on_progress(emit):
    def callback(state: str, value: float, detail: str) -> None:
        emit(DownloadEvent(state, value, detail))

    return callback


def _raise_if_stopped(stop) -> None:
    if stop():
        raise DownloadStopped()


def _fail(emit, kind: str, message: str) -> None:
    emit(DownloadEvent("error", 0.0, message))
    raise DownloadError(kind, message)

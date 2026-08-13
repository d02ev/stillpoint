"""The isolated YouTube download surface.

Everything that touches the yt-dlp library lives here. The module imports no
yt-dlp at top level, so tests can import it and exercise the pure helpers (URL
shape, error classification, option building) headlessly with no network and no
third-party package. The only yt-dlp call site is :func:`fetch_audio`.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.parse
from pathlib import Path

# -- standard format (contracts/download-service.md) -------------------------

#: The project's standard audio format. Downloads land in media/ as .m4a/AAC.
STANDARD_AUDIO_EXT = ".m4a"

#: AAC bitrate floor in kbps; matches the render pipeline's ``-b:a 192k`` so the
#: stored file and the export encode identically (Constitution III).
AAC_BITRATE = "192"

#: Audio-only selection, preferring a native m4a stream (no re-encode).
EXTRACT_FORMAT = "bestaudio[ext=m4a]/bestaudio"

#: Extensions counted as audio when deriving the downloaded-tracks list.
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".opus"}

# -- canonical plain-language strings (contracts) ------------------------------

BAD_LINK_MESSAGE = "We couldn't find downloadable music at that link."
NO_CONNECTION_MESSAGE = "Downloading music needs the internet. Try again when you're back online."
OTHER_MESSAGE = "Something went wrong downloading the music. Please try again."

# -- errors ---------------------------------------------------------------------


class DownloadError(Exception):
    """A download failure classified for the user: bad_link/no_connection/other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class DownloadStopped(Exception):
    """Raised when the user asks to stop the running download."""


# -- URL shape -------------------------------------------------------------------


_VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def is_video_url(url: str) -> bool:
    """True when the link looks like a single YouTube video.

    Accepts ``youtube.com/watch?v=…``, ``youtu.be/…``, and the ``/shorts/``,
    ``/embed/``, ``/live/`` watch pages. Rejects playlists, channel/home pages,
    and anything that is not a YouTube host. This is a fast pre-check; the real
    availability check happens inside yt-dlp.
    """
    try:
        parts = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if host not in _VIDEO_HOSTS:
        return False
    path = parts.path.rstrip("/")
    if host == "youtu.be":
        ident = path.lstrip("/")
        return bool(ident) and "/" not in ident and not parts.query.lower().startswith("list=")
    if path.startswith(("/shorts/", "/embed/", "/live/")):
        return True
    if path == "/watch":
        query = urllib.parse.parse_qs(parts.query)
        return bool(query.get("v") and query["v"][0])
    return False


# -- error classification ---------------------------------------------------------


def classify_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to exactly one user-facing bucket + plain message.

    Returns ``(kind, message)`` with kind in ``bad_link``, ``no_connection``,
    ``other`` (research Decision 6). Pure — fabricated exceptions in tests.
    """
    if isinstance(exc, DownloadStopped):
        raise exc
    if isinstance(exc, DownloadError):
        return exc.kind, exc.message
    message = str(exc).lower()
    if _is_no_connection(exc, message):
        return "no_connection", NO_CONNECTION_MESSAGE
    if _is_bad_link(message):
        return "bad_link", BAD_LINK_MESSAGE
    return "other", OTHER_MESSAGE


def _is_no_connection(exc: Exception, message: str) -> bool:
    if isinstance(
        exc, (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout, ssl.SSLError)
    ):
        return True
    return any(
        token in message
        for token in (
            "timed out",
            "timeout",
            "failed to resolve",
            "name or service not known",
            "connection refused",
            "network is unreachable",
            "getaddrinfo failed",
            "temporary failure in name resolution",
            "no route to host",
            "ssl: certificate",
        )
    )


def _is_bad_link(message: str) -> bool:
    if "unsupported url" in message:
        return True
    return any(
        token in message
        for token in (
            "no longer available",
            "video unavailable",
            "private video",
            "this video is private",
            "is unavailable",
            "has been removed",
            "deleted video",
            "not available in your country",
            "not available",
            "http error 404",
            "http error 410",
            "could not get video url",
        )
    )


# -- download options ---------------------------------------------------------------


def build_download_options(out_template: str, progress_cb, should_stop) -> dict:
    """The yt-dlp options dict (pure; no yt-dlp import required).

    ``progress_cb`` is called as ``(state, value, detail)`` for downloading
    progress; ``should_stop()`` is polled on every progress update.
    """

    def _hook(data: dict) -> None:
        if should_stop and should_stop():
            raise DownloadStopped()
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            fraction = downloaded / total if total else 0.0
            percent = min(99, int(fraction * 100))
            if progress_cb:
                progress_cb("downloading", fraction, f"Downloading … {percent}%")

    return {
        "format": EXTRACT_FORMAT,
        "outtmpl": str(out_template),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_hook],
    }


# -- the yt-dlp call site -------------------------------------------------------------


def fetch_audio(url: str, temp_dir, *, on_progress=None, should_stop=None) -> tuple[str, Path]:
    """Download the best audio stream for ``url`` into ``temp_dir``.

    Returns ``(title, produced_file)``. Raises :class:`DownloadError`
    (classified: bad_link / no connection / other) or :class:`DownloadStopped`.
    The only place in the app that imports yt-dlp.
    """
    import yt_dlp

    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(temp_dir / "%(title)s.%(ext)s")
    options = build_download_options(out_template, on_progress, should_stop)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadStopped:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = classify_error(exc)
        raise DownloadError(kind, message) from exc

    files = [p for p in temp_dir.iterdir() if p.is_file()]
    if not files:
        raise DownloadError("other", OTHER_MESSAGE)
    title = str(info.get("title") or "untitled")
    return title, files[0]

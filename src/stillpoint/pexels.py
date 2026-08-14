"""The isolated Pexels image service.

Pure Python: no Tk, no threading, no network at import time. Mirrors the
audio feature's ``youtube.py`` / ``download.py`` split — injectable fetchers
let headless tests drive every path with no network. All user-facing strings
are canonical here and imported by the UI, never re-typed (Constitution I).
"""

from __future__ import annotations

import io
import json
import os
import socket
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import media, names

# -- standard format (contracts/pexels-service.md) -----------------------------

#: The project's standard image format: 1920x1080 JPEG stored in media/.
STANDARD_IMAGE_SIZE = (1920, 1080)
STANDARD_IMAGE_EXT = ".jpg"
THUMBNAIL_SIZE = (480, 270)

#: Extensions counted as images when deriving the downloaded-images list.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

#: How many results the search returns by default.
DEFAULT_PER_PAGE = 12

# -- canonical plain-language strings (contracts/pexels-service.md) ------------

SEARCH_PLACEHOLDER = "Type what you want to find"
SEARCHING_MESSAGE = "Searching for pictures"
NO_RESULTS_MESSAGE = "No pictures found for that. Try different words."
SEARCH_ERROR_NO_CONNECTION = "Finding pictures needs the internet. Try again when you're back online."
SEARCH_ERROR_OTHER = "Something went wrong finding pictures. Please try again."
DOWNLOADING_MESSAGE = "Downloading the picture"
DOWNLOAD_DONE_MESSAGE = "Done."
DOWNLOAD_ERROR_NO_CONNECTION = "Downloading pictures needs the internet. Try again when you're back online."
DOWNLOAD_ERROR_OTHER = "Something went wrong downloading the picture. Please try again."
WAIT_FOR_JOB_MESSAGE = "Wait a moment for the first to finish."
PREVIEW_LOADING_MESSAGE = "Loading the picture…"
PREVIEW_ERROR_MESSAGE = "The picture couldn't be loaded. Check that the internet is on, then try again."
LIBRARY_TITLE = "Your pictures"
LIBRARY_EMPTY_MESSAGE = "No pictures downloaded yet."
CURRENT_PICTURE_TITLE = "Current picture"
CURRENT_PICTURE_EMPTY = "No picture set yet."
PICTURE_ROW_TITLE = "Background picture"
PICTURE_ROW_EMPTY_ACTION = "Find a picture"
PICTURE_ROW_CHOOSE_HINT = "Choose another"
BACKGROUND_MARKER = " — the background"
SEARCH_BUTTON_LABEL = "Search"
RESULT_PREVIEW_LABEL = "Preview"
RESULT_DOWNLOAD_LABEL = "Download"
PREVIEW_TITLE = "Preview"

#: Used by the editor when a background cannot be set (internal disk race).
OTHER_MESSAGE = "Something went wrong setting the picture as the background. Please try again."

# -- errors ----------------------------------------------------------------------


class PexelsError(Exception):
    """An image-service failure classified for the user: no_connection / other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# -- data model --------------------------------------------------------------------


@dataclass
class Photo:
    """One Pexels search result, as the app needs it."""

    id: int
    alt: str
    photographer: str
    width: int
    height: int
    base_url: str  # CDN URL, query stripped


# -- key resolution -------------------------------------------------------------


def resolve_api_key() -> str | None:
    """The Pexels API key, or ``None`` when none is configured.

    Priority: ``STILLPOINT_PEXELS_KEY`` env var, then the gitignored
    ``pexels_key.PEXELS_API_KEY`` baked in on the build machine (Constitution V).
    A missing module or key never raises — callers treat ``None`` as "other".
    """
    value = os.environ.get("STILLPOINT_PEXELS_KEY")
    if value:
        return value
    try:
        from . import pexels_key
    except Exception:  # noqa: BLE001 - never crash on a missing key module
        return None
    return pexels_key.PEXELS_API_KEY or None


# -- URL builders ------------------------------------------------------------------


def thumbnail_url(photo: Photo) -> str:
    """The 480x270 cover-crop of ``photo`` for result rows."""
    return photo.base_url + "?auto=compress&cs=tinysrgb&fit=crop&w=480&h=270&dpr=1"


def preview_url(photo: Photo) -> str:
    """The 1920x1080 cover-crop of ``photo`` for the preview pop-out."""
    return photo.base_url + "?auto=compress&cs=tinysrgb&fit=crop&w=1920&h=1080&dpr=1"


def download_url(photo: Photo) -> str:
    """The picture a download stores — the SAME 1920x1080 crop as preview.

    Preview and download fetch the same picture so the stored background is
    exactly what the user previewed (Constitution III).
    """
    return preview_url(photo)


def _strip_query(url: str) -> str:
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


# -- error classification ---------------------------------------------------------


_NO_CONNECTION_MESSAGES = {
    "search": SEARCH_ERROR_NO_CONNECTION,
    "download": DOWNLOAD_ERROR_NO_CONNECTION,
    "preview": PREVIEW_ERROR_MESSAGE,
}

_OTHER_MESSAGES = {
    "search": SEARCH_ERROR_OTHER,
    "download": DOWNLOAD_ERROR_OTHER,
    "preview": PREVIEW_ERROR_MESSAGE,
}

_NO_CONNECTION_TOKENS = (
    "timed out",
    "timeout",
    "failed to resolve",
    "name or service not known",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "no route to host",
    "ssl: certificate",
)


def _classify_kind(exc) -> str:
    """The failure bucket for ``exc``: ``no_connection`` | ``other``.

    HTTP 401/403/429 and everything else (including a missing key) are
    "other"; network failures are "no_connection". ``HTTPError`` must be
    checked before ``URLError`` (it is a subclass).
    """
    if isinstance(exc, urllib.error.HTTPError):
        return "other"
    if isinstance(
        exc,
        (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError),
    ):
        return "no_connection"
    message = str(exc).lower()
    return "no_connection" if any(token in message for token in _NO_CONNECTION_TOKENS) else "other"


def classify_error(exc, *, action: str) -> str:
    """Map an exception to exactly one user-facing plain message.

    ``action`` selects the message set: ``"search"`` | ``"download"`` |
    ``"preview"``. Preview always lands on ``PREVIEW_ERROR_MESSAGE``. HTTP
    401/403/429 and anything else (including a missing key) land on the "other"
    retry message; network failures land on the no-connection message. Never
    leaks status codes, keys, or tracebacks (Constitution I, FR-004/FR-022).
    """
    if action not in ("search", "download", "preview"):
        raise ValueError(f"unknown action: {action!r}")
    kind = _classify_kind(exc)
    if kind == "no_connection":
        return _NO_CONNECTION_MESSAGES[action]
    return _OTHER_MESSAGES[action]


def _classify(exc, action: str) -> tuple[str, str]:
    """The ``(kind, message)`` pair for a classified failure (internal use)."""
    return _classify_kind(exc), classify_error(exc, action=action)


# -- default fetchers ---------------------------------------------------------------


_USER_AGENT = "Stillpoint/1.0"


def _default_fetch(url: str, headers: dict) -> str:
    """Fetch ``url`` and return its text body (the search JSON)."""
    merged = {"User-Agent": _USER_AGENT, **headers}
    request = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _default_fetch_bytes(url: str) -> bytes:
    """Fetch ``url`` and return its raw bytes (the CDN picture)."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_image_bytes(url: str) -> bytes:
    """Fetch a CDN picture's raw bytes (used for search thumbnails)."""
    return _default_fetch_bytes(url)


# -- search ---------------------------------------------------------------------------


def _photo_from_dict(data: dict) -> Photo:
    photo_id = int(data.get("id") or 0)
    src = data.get("src") or {}
    base = src.get("large2x") or src.get("original") or ""
    return Photo(
        id=photo_id,
        alt=str(data.get("alt") or ""),
        photographer=str(data.get("photographer") or ""),
        width=int(data.get("width") or 0),
        height=int(data.get("height") or 0),
        base_url=_strip_query(base),
    )


def search_images(query, *, key=None, per_page: int = DEFAULT_PER_PAGE, fetch=None) -> list[Photo]:
    """Search Pexels and return up to ``per_page`` photos.

    ``query`` may be blank → returns ``[]`` without a network call (the panel
    shows its plain prompt instead). ``key`` may be ``None`` (the default) and
    is resolved via :func:`resolve_api_key`; a missing key is the classified
    "other" error, never a crash. ``fetch(url, headers)`` is injectable for
    headless tests. Raises only :class:`PexelsError`.
    """
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    query = (query or "").strip()
    if not query:
        return []
    if key is None:
        key = resolve_api_key()
    if not key:
        raise PexelsError("other", SEARCH_ERROR_OTHER)
    fetch = fetch or _default_fetch
    url = (
        "https://api.pexels.com/v1/search?"
        + urllib.parse.urlencode({"query": query, "per_page": per_page})
    )
    headers = {"Authorization": key}
    try:
        body = fetch(url, headers)
    except PexelsError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = _classify(exc, "search")
        raise PexelsError(kind, message) from exc
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise PexelsError("other", SEARCH_ERROR_OTHER) from exc
    return [_photo_from_dict(item) for item in data.get("photos") or []]


# -- download / preview --------------------------------------------------------------


def list_downloaded_images(project) -> list[str]:
    """Image files in the project's media/ folder, newest first.

    Derived from the folder itself (the 003 Decision 7 pattern): every
    downloaded image and any other image the user placed in media/ appears,
    and a file deleted outside the app drops from the list.
    """
    media_dir = project.media_dir()
    if not media_dir.is_dir():
        return []
    images = [
        p
        for p in media_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in images]


def _photo_filename(media_dir: Path, photo: Photo) -> str:
    """The deterministic filename for ``photo``; reuses an existing download.

    ``sanitize(alt or "pexels-{id}")-{id}.jpg`` — a re-download of the same
    photo lands on the same file (FR-012) instead of creating a duplicate.
    """
    stem = f"{names.sanitize_filename(photo.alt or f'pexels-{photo.id}')}-{photo.id}"
    existing = media_dir / (stem + STANDARD_IMAGE_EXT)
    if existing.is_file():
        return existing.name
    return names.unique_filename(media_dir, stem, STANDARD_IMAGE_EXT)


def download_photo(project, photo: Photo, *, key=None, fetch=None) -> str:
    """Fetch ``photo`` and store exactly one 1920x1080 JPEG in media/.

    Returns the stored filename. The bytes are cover-cropped by the CDN and
    normalized through ``media.import_image`` (quality 88). Storage is atomic:
    the file appears in ``media/`` only when fully normalized, and a failed or
    abandoned download leaves nothing behind. ``fetch(url)`` is injectable.
    Raises only :class:`PexelsError`.
    """
    fetch_bytes = fetch or _default_fetch_bytes
    media_dir = project.media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = _photo_filename(media_dir, photo)
    if (media_dir / filename).is_file():
        return filename
    fd, tmp_name = tempfile.mkstemp(prefix=".stillpoint-img-", suffix=".tmp", dir=media_dir)
    os.close(fd)
    staging = Path(tmp_name)
    out = Path(tmp_name + ".out")  # no image extension: never scanned as a library item
    try:
        data = fetch_bytes(download_url(photo))
        staging.write_bytes(data)
        media.import_image(staging, out, *STANDARD_IMAGE_SIZE)
        os.replace(out, media_dir / filename)
        return filename
    except PexelsError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = _classify(exc, "download")
        raise PexelsError(kind, message) from exc
    finally:
        for path in (staging, out):
            try:
                path.unlink()
            except OSError:
                pass


def preview_photo(photo: Photo, *, fetch=None) -> Image.Image:
    """Fetch ``photo``'s 1920x1080 cover-crop and return it as RGB.

    Never writes to disk (FR-008). ``fetch(url)`` is injectable for headless
    tests. Raises only :class:`PexelsError` (always ``PREVIEW_ERROR_MESSAGE``).
    """
    fetch_bytes = fetch or _default_fetch_bytes
    try:
        data = fetch_bytes(download_url(photo))
    except PexelsError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = _classify(exc, "preview")
        raise PexelsError(kind, message) from exc
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - decoded bytes were not a picture
        raise PexelsError("other", PREVIEW_ERROR_MESSAGE) from exc

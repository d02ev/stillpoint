"""Local-audio import orchestration: transient job state, error buckets, and
the import service.

Pure, display-free logic (no Tk, no ffmpeg at module top). The conversion is
injected so every rule (original never touched, atomic staging, no time cap
with progress, plain error buckets, unique names) is unit-testable headlessly
with a fake converter and no real ffmpeg (research Decision 5).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import media, names

#: The project's standard audio format (Constitution III parity with download).
STANDARD_AUDIO_EXT = ".m4a"

# -- canonical plain-language strings (contracts/import-service.md, import-ui.md) --

IMPORTING = "Importing your audio …"
UNREADABLE_MESSAGE = "We couldn't read that file as audio. Pick an audio file and try again."
OTHER_MESSAGE = "Something went wrong importing the audio. Please try again."
WAIT_MESSAGE = "Please wait — the other audio is still being imported."

#: ffmpeg demux/decode messages that mean "this isn't audio we can read".
_UNREADABLE_TOKENS = (
    "invalid data found",
    "moov atom",
    "no audio streams",
    "stream 0 is not audio",
    "could not find codec parameters",
    "invalid data",
    "decode failed",
    "error while decoding",
    "malformed file",
    "not a valid audio",
)


def importing_line(fraction: float) -> str:
    """The plain progress line for a 0..1 fraction (0 → indeterminate)."""
    if fraction and fraction > 0:
        percent = min(99, int(fraction * 100))
        return f"Importing your audio … {percent}%"
    return IMPORTING


# -- errors ---------------------------------------------------------------------


class ImportError(Exception):
    """An import failure classified for the user: unreadable | other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class ImportEvent:
    """One job event the GUI renders verbatim.

    ``state`` is ``importing | done | error``; ``value`` is a 0..1 fraction for
    ``importing`` when the duration is known (0 = indeterminate); ``detail`` is
    the plain-language line to render — for ``done`` it carries the stored
    filename (contracts/import-ui.md).
    """

    state: str
    value: float = 0.0
    detail: str = ""


# -- error classification ---------------------------------------------------------


def classify_import_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to exactly one user-facing bucket + plain message.

    Returns ``(kind, message)`` with kind in ``unreadable``, ``other``
    (research Decision 5). Never a raw traceback. Pure — fabricated exceptions
    in tests.
    """
    if isinstance(exc, ImportError):
        return exc.kind, exc.message
    if isinstance(exc, (PermissionError, FileNotFoundError, OSError)):
        return "unreadable", UNREADABLE_MESSAGE
    message = str(exc).lower()
    if any(token in message for token in _UNREADABLE_TOKENS):
        return "unreadable", UNREADABLE_MESSAGE
    return "other", OTHER_MESSAGE


# -- the import service -----------------------------------------------------------


def import_local_audio(project, source, *, progress=None, convert=None) -> str:
    """Convert a local audio file into the project's standard format.

    Stages the conversion in a temp directory inside ``media/``, moves the
    finished file into place with ``os.replace`` under a unique name, and
    removes the temp directory in a ``finally``. Emits :class:`ImportEvent`
    objects through ``progress`` as the job advances and returns the stored
    filename on success. On failure raises :class:`ImportError` (unreadable |
    other) with the project and the original file byte-for-byte unchanged
    (FR-005, FR-008, FR-010, FR-012).
    """
    emit = progress or (lambda _event: None)
    convert = convert or media.convert_to_m4a
    source = Path(source)

    if not source.is_file():
        emit(ImportEvent("error", 0.0, UNREADABLE_MESSAGE))
        raise ImportError("unreadable", UNREADABLE_MESSAGE)

    media_dir = project.media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".stillpoint-import-", dir=media_dir))
    try:
        staging = temp_dir / ("converted" + STANDARD_AUDIO_EXT)
        emit(ImportEvent("importing", 0.0, IMPORTING))
        convert(
            source,
            staging,
            progress_cb=lambda fraction: emit(ImportEvent("importing", fraction, importing_line(fraction))),
            timeout=None,
        )
        stem = names.sanitize_filename(source.stem)
        filename = names.unique_filename(media_dir, stem, STANDARD_AUDIO_EXT)
        os.replace(staging, media_dir / filename)
        emit(ImportEvent("done", 0.0, filename))
        return filename
    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified for the user below
        kind, message = classify_import_error(exc)
        emit(ImportEvent("error", 0.0, message))
        raise ImportError(kind, message) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

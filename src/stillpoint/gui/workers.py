"""Background workers: run long work off the UI thread and report progress and
completion through thread-safe queues.

``RenderWorker`` drives ffmpeg for an export; ``DownloadWorker`` drives a
YouTube audio download; ``SearchWorker`` / ``ImageDownloadWorker`` /
``PreviewImageWorker`` drive the Pexels image panel. The GUI polls any of them
with ``root.after`` and never blocks the window (Constitution II).
"""

from __future__ import annotations

import io
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .. import download, import_audio, model as model_mod, render


@dataclass
class RenderStatus:
    state: str  # 'progress' | 'done' | 'error'
    value: float | str = 0.0


@dataclass
class PreviewStatus:
    state: str  # 'done' | 'error'
    value: str | Exception = ""  # done → WAV path; error → the exception/str


class PreviewWorker:
    """Bakes the preview mix WAV in a daemon thread; poll poll() from the UI.

    Mirrors ``ImportWorker``: the bake runs off the UI thread and the GUI polls
    with ``root.after`` while BAKING — the window never blocks and two bakes
    never contend (the editor disables the control during BAKING, FR-008/009).
    The WAV is written to the system temp, outside the project (research
    Decision 9, FR-012).
    """

    def __init__(self, project: model_mod.Project, out_path: Path, *, baker=None):
        self._queue: "queue.Queue[PreviewStatus]" = queue.Queue()
        self._project = project
        self._out = out_path
        self._baker = baker
        self._thread = threading.Thread(target=self._run, name="stillpoint-preview-bake", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> PreviewStatus | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            if self._baker is not None:
                self._baker(self._project, self._out)
            else:
                from .. import mix

                mix.render_mix(self._project, self._out)
            self._queue.put(PreviewStatus("done", str(self._out)))
        except Exception as exc:  # noqa: BLE001 - bucketed by classify_playback_error
            self._queue.put(PreviewStatus("error", exc))


class RenderWorker:
    """Runs a render in a daemon thread; poll poll() from the UI."""

    def __init__(self, project: model_mod.Project, out_path: Path, project_snapshot=None):
        self._queue: "queue.Queue[RenderStatus]" = queue.Queue()
        # Render against the live project; the thread only reads media files.
        self._project = project
        self._out = out_path
        self._thread = threading.Thread(target=self._run, name="stillpoint-render", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> RenderStatus | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            render.render_with_progress(
                self._project,
                self._out,
                progress_cb=lambda f: self._queue.put(RenderStatus("progress", f)),
            )
            self._queue.put(RenderStatus("done", str(self._out)))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            self._queue.put(RenderStatus("error", str(exc)))


class DownloadWorker:
    """Runs download_track in a daemon thread; poll poll() from the UI.

    ``stop()`` sets a thread-safe flag that aborts the running fetch; any temp
    files are deleted and no partial track ever reaches the project.
    """

    def __init__(self, project: model_mod.Project, url: str, *, fetch=None):
        self._queue: "queue.Queue[download.DownloadEvent]" = queue.Queue()
        self._stop = threading.Event()
        self._project = project
        self._url = url
        self._fetch = fetch
        self._thread = threading.Thread(target=self._run, name="stillpoint-download", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> download.DownloadEvent | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            download.download_track(
                self._project,
                self._url,
                progress=lambda event: self._queue.put(event),
                should_stop=self._stop.is_set,
                fetch=self._fetch,
            )
        except download.DownloadStopped:
            pass  # the 'stopped' event is already queued
        except download.DownloadError:
            pass  # the 'error' event is already queued
        except Exception as exc:  # noqa: BLE001 - never surface a traceback
            from .. import youtube

            self._queue.put(download.DownloadEvent("error", 0.0, youtube.OTHER_MESSAGE))


class ImportWorker:
    """Runs import_local_audio in a daemon thread; poll poll() from the UI.

    Mirrors ``DownloadWorker`` minus the stop flag — import is run-to-completion
    (FR-009). The one-import-at-a-time rule is guarded by the editor.
    """

    def __init__(self, project: model_mod.Project, source: str, *, convert=None):
        self._queue: "queue.Queue[import_audio.ImportEvent]" = queue.Queue()
        self._project = project
        self._source = source
        self._convert = convert
        self._thread = threading.Thread(target=self._run, name="stillpoint-import", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> import_audio.ImportEvent | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            import_audio.import_local_audio(
                self._project,
                self._source,
                progress=lambda event: self._queue.put(event),
                convert=self._convert,
            )
        except import_audio.ImportError:
            pass  # the 'error' event is already queued
        except Exception as exc:  # noqa: BLE001 - never surface a traceback
            kind, message = import_audio.classify_import_error(exc)
            self._queue.put(import_audio.ImportEvent("error", 0.0, message))


@dataclass
class SearchEvent:
    """One image-search event the panel renders verbatim.

    ``state`` is ``searching | done | empty | error``; ``photos`` carries the
    results on ``done``; ``thumbs`` maps each photo id to its decoded RGB
    thumbnail (built in the worker, never the UI thread); ``detail`` is the
    canonical plain-language line for ``searching``/``empty``/``error``.
    """

    state: str
    photos: list = field(default_factory=list)
    thumbs: dict = field(default_factory=dict)
    detail: str = ""


@dataclass
class ImageDownloadEvent:
    """One image-download event: ``downloading | done | error``.

    ``value`` is the stored filename on ``done``; ``detail`` is the canonical
    plain-language line.
    """

    state: str
    value: str = ""
    detail: str = ""


@dataclass
class PreviewEvent:
    """One preview event: ``loading | shown | error``.

    ``value`` is the RGB PIL image on ``shown``; ``detail`` is the canonical
    plain-language line.
    """

    state: str
    value: object = None
    detail: str = ""


class SearchWorker:
    """Runs pexels.search_images plus thumbnail decoding in a daemon thread.

    The search fetch and every thumbnail fetch/decode happen here, never on the
    UI thread (Constitution II). Poll ``poll()`` from the UI with ``root.after``.
    Only canonical messages ever reach the panel.
    """

    def __init__(self, query: str, key=None, *, fetch=None, thumb_fetch=None):
        self._queue: "queue.Queue[SearchEvent]" = queue.Queue()
        self._query = query
        self._key = key
        self._fetch = fetch
        self._thumb_fetch = thumb_fetch
        self._thread = threading.Thread(target=self._run, name="stillpoint-image-search", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> SearchEvent | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        from .. import pexels

        try:
            self._queue.put(SearchEvent("searching", detail=pexels.SEARCHING_MESSAGE))
            photos = pexels.search_images(self._query, key=self._key, fetch=self._fetch)
            if not photos:
                self._queue.put(SearchEvent("empty", detail=pexels.NO_RESULTS_MESSAGE))
                return
            fetch_bytes = self._thumb_fetch or pexels.fetch_image_bytes
            thumbs: dict[int, Image.Image] = {}
            for photo in photos:
                image = _decode_image(fetch_bytes, pexels.thumbnail_url(photo))
                if image is not None:
                    thumbs[photo.id] = image
            if not thumbs:
                self._queue.put(SearchEvent("empty", detail=pexels.NO_RESULTS_MESSAGE))
                return
            self._queue.put(SearchEvent("done", photos=photos, thumbs=thumbs))
        except pexels.PexelsError as exc:
            self._queue.put(SearchEvent("error", detail=exc.message))
        except Exception:  # noqa: BLE001 - never surface a traceback
            self._queue.put(SearchEvent("error", detail=pexels.SEARCH_ERROR_OTHER))


class ImageDownloadWorker:
    """Runs pexels.download_photo in a daemon thread; poll poll() from the UI."""

    def __init__(self, project: model_mod.Project, photo, key=None, *, fetch=None):
        self._queue: "queue.Queue[ImageDownloadEvent]" = queue.Queue()
        self._project = project
        self._photo = photo
        self._key = key
        self._fetch = fetch
        self._thread = threading.Thread(target=self._run, name="stillpoint-image-download", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> ImageDownloadEvent | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        from .. import pexels

        try:
            self._queue.put(ImageDownloadEvent("downloading", detail=pexels.DOWNLOADING_MESSAGE))
            filename = pexels.download_photo(self._project, self._photo, key=self._key, fetch=self._fetch)
            self._queue.put(ImageDownloadEvent("done", value=filename, detail=pexels.DOWNLOAD_DONE_MESSAGE))
        except pexels.PexelsError as exc:
            self._queue.put(ImageDownloadEvent("error", detail=exc.message))
        except Exception:  # noqa: BLE001 - never surface a traceback
            self._queue.put(ImageDownloadEvent("error", detail=pexels.DOWNLOAD_ERROR_OTHER))


class PreviewImageWorker:
    """Runs pexels.preview_photo in a daemon thread; never writes to disk."""

    def __init__(self, photo, *, fetch=None):
        self._queue: "queue.Queue[PreviewEvent]" = queue.Queue()
        self._photo = photo
        self._fetch = fetch
        self._thread = threading.Thread(target=self._run, name="stillpoint-image-preview", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> PreviewEvent | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        from .. import pexels

        try:
            self._queue.put(PreviewEvent("loading", detail=pexels.PREVIEW_LOADING_MESSAGE))
            image = pexels.preview_photo(self._photo, fetch=self._fetch)
            self._queue.put(PreviewEvent("shown", value=image))
        except pexels.PexelsError as exc:
            self._queue.put(PreviewEvent("error", detail=exc.message))
        except Exception:  # noqa: BLE001 - never surface a traceback
            self._queue.put(PreviewEvent("error", detail=pexels.PREVIEW_ERROR_MESSAGE))


def _decode_image(fetch_bytes, url: str) -> Image.Image | None:
    """Fetch ``url`` and decode it as an RGB image, or ``None`` on any failure."""
    try:
        data = fetch_bytes(url)
    except Exception:  # noqa: BLE001 - a failed thumbnail is skipped, not fatal
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.convert("RGB")
    except Exception:  # noqa: BLE001 - bytes were not a decodable picture
        return None

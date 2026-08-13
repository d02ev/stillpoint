"""Background workers: run long work off the UI thread and report progress and
completion through thread-safe queues.

``RenderWorker`` drives ffmpeg for an export; ``DownloadWorker`` drives a
YouTube audio download. The GUI polls either with ``root.after`` and never
blocks the window (Constitution II).
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import download, import_audio, model as model_mod, render


@dataclass
class RenderStatus:
    state: str  # 'progress' | 'done' | 'error'
    value: float | str = 0.0


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

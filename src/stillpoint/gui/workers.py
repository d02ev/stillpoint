"""Background render worker: runs ffmpeg off the UI thread and reports progress
and completion through thread-safe queues."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import model as model_mod, render


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

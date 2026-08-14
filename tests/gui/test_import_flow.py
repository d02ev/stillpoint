"""GUI flow tests for the local-audio import (spec 004).

These build the real frame on the shared ``tk_root`` fixture and drive the
import flow with the native picker, the worker, and the dialogs monkeypatched
so no modal window ever blocks and no real conversion ever runs (research
Decision 7). Because the editor drains the worker's queue synchronously on the
first poll, each scripted flow completes without pumping the Tk event loop.
"""

import pytest

from stillpoint import model as model_mod
from stillpoint.gui.app import App
from stillpoint.gui.channels import MUSIC_ROLE, VOICE_ROLE
from stillpoint.import_audio import IMPORTING, OTHER_MESSAGE, UNREADABLE_MESSAGE, WAIT_MESSAGE, ImportEvent
from stillpoint.playback import PlaybackSession


@pytest.fixture
def app(tk_root):
    instance = App(tk_root)
    yield instance, tk_root
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def fake_session(monkeypatch):
    """Route the editor's real PlaybackSession to a sink that touches nothing real."""

    class FakeSink:
        def __init__(self):
            self.calls = []
            self._pos_seconds = 0.0

        def open(self, path, *, start_seconds=0.0):
            self.calls.append(("open", str(path), start_seconds))

        def play(self):
            self.calls.append(("play",))

        def pause(self):
            self.calls.append(("pause",))

        def resume(self):
            self.calls.append(("resume",))

        def restart(self):
            self.calls.append(("restart",))

        def done(self):
            return False

        def position_seconds(self):
            return self._pos_seconds

        def stop(self):
            self.calls.append(("stop",))

    from stillpoint.gui import editor as editor_mod

    def _factory():
        return PlaybackSession(sink=FakeSink())

    monkeypatch.setattr(editor_mod, "PlaybackSession", _factory)
    return _factory


@pytest.fixture
def fake_preview_worker(monkeypatch):
    """Scripted PreviewWorker: every bake is 'done' with a bogus WAV path."""
    from stillpoint.gui import editor as editor_mod
    from stillpoint.gui.workers import PreviewStatus

    instances = []

    class FakePreviewWorker:
        def __init__(self, project, out_path, **kwargs):
            self.out_path = out_path
            instances.append(self)

        def start(self):
            pass

        def poll(self):
            return PreviewStatus("done", self.out_path)

    monkeypatch.setattr(editor_mod, "PreviewWorker", FakePreviewWorker)
    return instances


def test_playback_coexists_with_in_flight_import(
    app, tmp_path, monkeypatch, fake_session, fake_preview_worker
):
    """US3 (T024): play/pause coexist with a running import — neither side
    interrupts or crashes the other, and no error dialog appears."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: "C:/voice.mp3")
    made = {}

    class ImportWorker:
        def __init__(self, project, path, *, convert=None):
            made["project"] = project
            self._once = True  # report "importing" once, then stay silent (in flight)

        def start(self):
            made["started"] = True

        def poll(self):
            if self._once:
                self._once = False
                return ImportEvent("importing", 0.3, IMPORTING)
            return None

    monkeypatch.setattr("stillpoint.gui.editor.ImportWorker", ImportWorker)
    editor._voice_row._on_import()
    assert editor._import_worker is not None  # the import is in flight

    editor._on_transport()  # press play mid-import
    root.update_idletasks()
    assert editor._transport.state == "pause"
    assert editor._playback is not None
    assert editor._playback.state == PlaybackSession.PLAYING
    assert fake_preview_worker  # the bake ran alongside the import
    assert editor._import_worker is not None  # the import is not interrupted
    assert made.get("started") is True
    assert noticed == []  # no error dialogs, ever
    assert instance.project.movie.voice is None  # project untouched by playback
    assert instance.project.movie.audio is not None


def _open_empty_project(instance, tmp_path, title="First Mix"):
    project = model_mod.new_project(title, tmp_path / title, "t0")
    instance.open_project(tmp_path / title)
    return project


def _start_import(row, instance, monkeypatch, events, source="C:/voice.mp3"):
    """Wire a scripted FakeWorker onto the editor's import and click the row."""
    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: source)
    created = {}

    class FakeWorker:
        def __init__(self, project, path, *, convert=None):
            created["project"] = project
            created["source"] = path
            self._events = list(events)

        def start(self):
            created["started"] = True

        def poll(self):
            return self._events.pop(0) if self._events else None

    monkeypatch.setattr("stillpoint.gui.editor.ImportWorker", FakeWorker)
    row._on_import()
    return created


def _body_texts(row):
    return [child.cget("text") for child in row._body.winfo_children()]


# -- User Story 1: voice path (FR-001/004/006) ---------------------------------


def test_voice_import_binds_to_voice_role(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    (project.media_dir() / "voice.m4a").write_bytes(b"x")
    editor = instance._editor

    _start_import(
        editor._voice_row, instance, monkeypatch,
        [ImportEvent("importing", 0.0, IMPORTING), ImportEvent("done", 0.0, "voice.m4a")],
    )
    assert editor._voice_row.state() == "loaded"
    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.voice is not None
    assert loaded.movie.voice.filename == "voice.m4a"
    assert loaded.movie.audio is None  # the music role is untouched


def test_voice_row_importing_while_worker_runs(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor

    _start_import(editor._voice_row, instance, monkeypatch, [ImportEvent("importing", 0.25, "Importing your audio … 25%")])
    assert editor._voice_row.state() == "importing"
    assert _body_texts(editor._voice_row) == ["Importing your audio … 25%"]
    assert project.movie.voice is None  # nothing assigned until done


def test_voice_import_error_returns_to_empty(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    _start_import(editor._voice_row, instance, monkeypatch, [ImportEvent("error", 0.0, UNREADABLE_MESSAGE)])
    assert editor._voice_row.state() == "empty"
    assert noticed and noticed[0][1] == UNREADABLE_MESSAGE
    assert project.movie.voice is None


def test_voice_import_setter_failure_shows_other_message(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    # The done event names a file that is not actually in media/ → ValueError.
    _start_import(editor._voice_row, instance, monkeypatch, [ImportEvent("done", 0.0, "gone.m4a")])
    assert editor._voice_row.state() == "empty"
    assert noticed and noticed[0][1] == OTHER_MESSAGE
    assert project.movie.voice is None


def test_second_import_while_in_flight_shows_wait_line(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    _start_import(editor._voice_row, instance, monkeypatch, [ImportEvent("importing", 0.0, IMPORTING)])
    assert editor._import_worker is not None

    picks = []
    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: picks.append("x") or "C:/again.mp3")
    editor._voice_row._on_import()
    assert picks == []  # the picker is never opened when an import is in flight
    assert noticed and noticed[0][1] == WAIT_MESSAGE
    assert project.movie.voice is None


def test_cancel_picker_changes_nothing(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: "")
    created = {}

    class FakeWorker:
        def __init__(self, *a, **k):
            created["made"] = True

        def start(self):
            pass

        def poll(self):
            return None

    monkeypatch.setattr("stillpoint.gui.editor.ImportWorker", FakeWorker)
    editor._voice_row._on_import()
    assert "made" not in created
    assert editor._voice_row.state() == "empty"
    assert project.movie.voice is None
    assert project.movie.audio is None


# -- User Story 2: music path (FR-002/006) --------------------------------------


def test_music_import_binds_to_music_role(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    (project.media_dir() / "song.m4a").write_bytes(b"x")
    editor = instance._editor

    _start_import(
        editor._music_row, instance, monkeypatch,
        [ImportEvent("importing", 0.0, IMPORTING), ImportEvent("done", 0.0, "song.m4a")],
        source="C:/song.mp3",
    )
    assert editor._music_row.state() == "loaded"
    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.audio is not None
    assert loaded.movie.audio.filename == "song.m4a"
    assert loaded.movie.voice is None  # the voice role is untouched


def test_music_import_error_returns_to_empty(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    _start_import(editor._music_row, instance, monkeypatch, [ImportEvent("error", 0.0, UNREADABLE_MESSAGE)])
    assert editor._music_row.state() == "empty"
    assert noticed and noticed[0][1] == UNREADABLE_MESSAGE
    assert project.movie.audio is None


def test_music_channel_download_action_still_opens_panel(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    from stillpoint.gui import panels

    editor._music_row._on_download()
    assert editor._panels.visible == panels.PANEL_DOWNLOAD


# -- User Story 4: progress rendering (FR-009) -----------------------------------


def test_importing_fraction_renders_percent_line(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    _start_import(
        editor._voice_row, instance, monkeypatch,
        [ImportEvent("importing", 0.0, IMPORTING), ImportEvent("importing", 0.63, "Importing your audio … 63%")],
    )
    assert editor._voice_row.state() == "importing"
    assert _body_texts(editor._voice_row) == ["Importing your audio … 63%"]


def test_importing_indeterminate_renders_base_line(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    _start_import(editor._music_row, instance, monkeypatch, [ImportEvent("importing", 0.0, IMPORTING)])
    assert editor._music_row.state() == "importing"
    assert _body_texts(editor._music_row) == [IMPORTING]

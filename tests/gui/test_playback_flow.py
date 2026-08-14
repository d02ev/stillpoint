"""US1 flow tests (T013): press play → bake → play; press again → pause;
failure during bake → plain dialog and the control returns to play.

The editor's real PlaybackSession is driven with an injected fake sink and fake
baker (no device, no ffmpeg), and its PreviewWorker is replaced with a scripted
fake. Assertions cover FR-002/011 and the STOPPED → BAKING → PLAYING ↔ PAUSED
route through the composed editor.
"""

import wave

import pytest

from stillpoint import model as model_mod
from stillpoint.gui.app import App
from stillpoint.gui.workers import PreviewStatus
from stillpoint.playback import OTHER_MESSAGE, PlaybackSession


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


class FakeBaker:
    def __call__(self, project, out_path, **kw):
        out_path = __import__("pathlib").Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"\x00" * 800)


@pytest.fixture
def app(tk_root):
    instance = App(tk_root)
    yield instance, tk_root
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def fake_session(monkeypatch):
    """Routes the editor's PlaybackSession to a session with fake sink/baker."""
    from stillpoint.gui import editor as editor_mod

    def _factory():
        return PlaybackSession(sink=FakeSink(), baker=FakeBaker())

    monkeypatch.setattr(editor_mod, "PlaybackSession", _factory)
    yield _factory


@pytest.fixture
def fake_preview_worker(monkeypatch):
    """Replaces editor.PreviewWorker with a scriptable fake (no real thread)."""
    from stillpoint.gui import editor as editor_mod

    class FakeWorker:
        script = []
        instances = []

        def __init__(self, project, out_path, *, baker=None):
            self.project = project
            self.out_path = out_path
            self.events = (
                list(FakeWorker.script)
                if FakeWorker.script
                else [PreviewStatus("done", str(out_path))]
            )
            FakeWorker.instances.append(self)

        def start(self):
            pass

        def poll(self):
            return self.events.pop(0) if self.events else None

    monkeypatch.setattr(editor_mod, "PreviewWorker", FakeWorker)
    yield FakeWorker
    FakeWorker.script = []
    FakeWorker.instances = []


def _open_with_music(instance, tmp_path):
    project = model_mod.new_project("Flow", tmp_path / "proj", "t0")
    instance.open_project(tmp_path / "proj")
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    return instance.project


def _session(editor):
    return editor._playback


# -- play → bake → play ---------------------------------------------------------


def test_play_bakes_then_plays(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor

    editor._on_transport()

    assert editor._transport.state == "pause"
    session = _session(editor)
    assert session is not None
    assert session.state == PlaybackSession.PLAYING
    assert fake_preview_worker.instances  # the bake worker ran
    assert session.sink.calls[:2] == [("open", str(fake_preview_worker.instances[0].out_path), 0.0), ("play",)]
    assert editor._transport.tooltip() == "Pause the preview"


def test_play_again_pauses(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor

    editor._on_transport()
    editor._on_transport()  # transport is in the pause state → pause

    session = _session(editor)
    assert session.state == PlaybackSession.PAUSED
    assert editor._transport.state == "play"
    assert editor._transport.tooltip() == "Resume the preview"
    assert session.sink.calls[-1] == ("pause",)


def test_resume_from_pause_never_from_top(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor

    editor._on_transport()
    editor._on_transport()  # pause
    before = list(_session(editor).sink.calls)
    editor._on_transport()  # play again → resume

    session = _session(editor)
    assert session.state == PlaybackSession.PLAYING
    calls = session.sink.calls[len(before):]
    assert ("resume",) in calls
    assert ("play",) not in calls  # never re-streams from the top


def test_no_bake_when_signature_unchanged(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor

    editor._on_transport()
    editor._on_transport()  # pause
    editor._on_transport()  # resume — same signature, no re-bake
    editor._on_transport()  # pause again
    editor._on_transport()  # resume again

    # Only the first press ran a bake; the rest replayed the baked WAV.
    assert len(fake_preview_worker.instances) == 1
    session = _session(editor)
    assert session.state == PlaybackSession.PLAYING


# -- failure during bake (FR-011) -----------------------------------------------


def test_bake_failure_shows_plain_dialog_and_returns_to_play(
    app, tmp_path, monkeypatch, fake_session, fake_preview_worker
):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))

    fake_preview_worker.script = [PreviewStatus("error", RuntimeError("boom"))]
    editor._on_transport()

    assert noticed and OTHER_MESSAGE in noticed[0][1]
    assert editor._transport.state == "play"
    assert editor._transport.tooltip() == "Play the preview from the top"
    session = _session(editor)
    assert session is None or session.state == PlaybackSession.STOPPED


def test_unreadable_bake_failure_message(app, tmp_path, monkeypatch, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    from stillpoint.playback import UNREADABLE_MESSAGE

    fake_preview_worker.script = [PreviewStatus("error", RuntimeError("invalid data found"))]
    editor._on_transport()

    assert noticed and UNREADABLE_MESSAGE in noticed[0][1]
    assert editor._transport.state == "play"


# -- User Story 4: resume / Start over / end-of-mix (FR-004) ---------------------


def test_start_over_returns_to_the_top(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor

    editor._on_transport()  # play
    editor._on_transport()  # pause partway
    session = _session(editor)
    before = list(session.sink.calls)
    editor._on_start_over()  # Start over (TDD: wired in T031)

    calls = session.sink.calls[len(before):]
    assert ("restart",) in calls  # discarded the paused spot, from the top
    assert ("resume",) not in calls
    assert session.state == PlaybackSession.PLAYING
    assert editor._transport.state == "pause"
    assert editor._transport.tooltip() == "Pause the preview"


def test_mix_end_returns_control_to_play_and_next_play_is_from_top(
    app, tmp_path, monkeypatch, fake_session, fake_preview_worker
):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor
    editor._on_transport()
    session = _session(editor)

    monkeypatch.setattr(session.sink, "done", lambda: True)
    editor._poll_playback()  # the mix reaches its end

    assert session.state == PlaybackSession.FINISHED
    assert editor._transport.state == "play"
    assert editor._transport.tooltip() == "Play the preview from the top"
    assert editor._playback_poll_id is None  # no further polling after the end

    editor._on_transport()  # play again → from the top, not a resume
    assert session.state == PlaybackSession.PLAYING
    tail = session.sink.calls
    assert tail[-2][0] == "open" and tail[-1] == ("play",)
    assert ("resume",) not in tail


# -- preparing is a visible transient, never stuck -------------------------------


def test_preparing_state_during_bake(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_with_music(instance, tmp_path)
    editor = instance._editor
    from stillpoint.gui import editor as editor_mod

    class DrainingWorker(fake_preview_worker):
        def poll(self):
            return None  # bake still running

    try:
        editor_mod.PreviewWorker = DrainingWorker
        editor._on_transport()
    finally:
        editor_mod.PreviewWorker = fake_preview_worker

    assert editor._transport.state == "preparing"
    assert editor._transport.tooltip() == "Preparing preview…"
    assert str(editor._transport._button.cget("state")) == "disabled"
    assert editor._transport.tooltip() != OTHER_MESSAGE  # never stuck, no error

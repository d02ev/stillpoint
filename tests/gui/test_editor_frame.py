"""GUI smoke tests for the composed editor frame (research Decision 6).

These build the real frame on the shared Tk root fixture and drive clicks with
monkeypatched dialogs/file pickers so no modal window ever blocks. The import
flow is driven with a scripted fake worker so no real conversion ever runs.
"""

import pytest

from stillpoint import model as model_mod
from stillpoint.gui import panels
from stillpoint.gui import transport
from stillpoint.gui.app import App
from stillpoint.import_audio import UNREADABLE_MESSAGE, ImportEvent
from stillpoint.playback import PlaybackSession


@pytest.fixture
def app(tk_root):
    instance = App(tk_root)
    yield instance, tk_root
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def fake_import_worker(monkeypatch):
    """Replaces editor.ImportWorker with a scriptable fake (no real thread)."""
    from stillpoint.gui import editor as editor_mod

    class FakeWorker:
        script = []
        instances = []

        def __init__(self, project, source, *, convert=None):
            self.project = project
            self.source = source
            self.events = list(FakeWorker.script)
            FakeWorker.instances.append(self)

        def start(self):
            pass

        def poll(self):
            return self.events.pop(0) if self.events else None

    monkeypatch.setattr(editor_mod, "ImportWorker", FakeWorker)
    yield FakeWorker
    FakeWorker.script = []
    FakeWorker.instances = []


@pytest.fixture
def fake_session(monkeypatch):
    """Route the editor's real PlaybackSession to a sink that touches nothing real."""
    from stillpoint.gui import editor as editor_mod
    from stillpoint.playback import PlaybackSession

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

    def _factory():
        session = PlaybackSession(sink=FakeSink())
        _factory.instances.append(session)
        return session

    _factory.instances = []
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


def _open_empty_project(instance, tmp_path, title="First Mix"):
    project = model_mod.new_project(title, tmp_path / title, "t0")
    instance.open_project(tmp_path / title)
    return project


# -- User Story 1: frame present on open --------------------------------------

def test_editor_frame_shows_title_rail_channels_transport_export(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    editor = instance._editor

    assert editor._title_label.cget("text") == "First Mix"
    assert len(editor._rail._buttons) == 3
    assert editor._music_row.state() == "empty"
    assert editor._voice_row.state() == "empty"
    assert str(editor._transport._button.cget("state")) == "disabled"
    assert editor._export.cget("text") == "Export"


def test_all_panels_closed_on_open(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    assert instance._editor._panels.visible is None
    assert instance._editor._panels.aim == "music"


def test_frame_uses_light_theme_colors(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    from stillpoint import theme

    editor = instance._editor
    assert editor.cget("bg") == theme.Palette.background
    assert editor._title_label.cget("bg") == theme.Palette.panel
    assert editor._music_row.cget("bg") == theme.Palette.panel


# -- User Story 2: rail toggling ----------------------------------------------

def test_rail_toggle_opens_and_closes_panel(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    rail = editor._rail

    rail._buttons[0].invoke()
    assert editor._panels.visible == panels.PANEL_IMAGE

    rail._buttons[0].invoke()
    assert editor._panels.visible is None


def test_rail_opens_one_panel_at_a_time(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    rail = editor._rail

    rail._buttons[0].invoke()
    rail._buttons[1].invoke()
    assert editor._panels.visible == panels.PANEL_DOWNLOAD


def test_rail_icon_switching_closes_previous(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    rail = editor._rail

    rail._buttons[0].invoke()
    rail._buttons[2].invoke()
    assert editor._panels.visible == panels.PANEL_ADJUSTMENT


# -- User Story 3: empty music channel actions ---------------------------------

def test_empty_music_channel_download_opens_panel(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    editor = instance._editor

    # Music row has exactly two actions in the body.
    from stillpoint.gui.channels import MUSIC_ROLE

    assert editor._music_row.state() == "empty"
    editor._open_download_panel()
    assert editor._panels.visible == panels.PANEL_DOWNLOAD


def test_empty_music_channel_import_starts_real_flow(app, tmp_path, monkeypatch, fake_import_worker):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))
    editor._music_row._on_import()
    assert editor._music_row.state() == "importing"
    assert fake_import_worker.instances
    assert fake_import_worker.instances[0].source == str(tmp_path / "a.mp3")


def test_import_cancel_shows_no_notice(app, tmp_path, monkeypatch, fake_import_worker):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: "")
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    editor._music_row._on_import()
    assert noticed == []
    assert not fake_import_worker.instances
    assert editor._music_row.state() == "empty"


def test_import_error_never_modifies_project(app, tmp_path, monkeypatch, fake_import_worker):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: None)
    fake_import_worker.script = [ImportEvent("error", 0.0, UNREADABLE_MESSAGE)]
    editor._music_row._on_import()
    assert project.movie.audio is None
    assert project.movie.voice is None
    assert editor._music_row.state() == "empty"
    assert not (project.media_dir() / "a.mp3").exists()


# -- User Story 4: empty voice channel has one action --------------------------

def test_empty_voice_channel_has_import_but_no_download(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    from stillpoint.gui.channels import VOICE_ROLE

    assert editor._voice_row.state() == "empty"
    editor._open_download_panel()  # direct: the voice row must never route here
    # The voice row simply has no download callback wired; opening via the rail
    # is fine, but the voice row itself offers no download action.
    from stillpoint.gui.channels import ChannelRow

    assert isinstance(editor._voice_row, ChannelRow)
    assert editor._voice_row._on_download is None


def test_voice_import_starts_real_flow(app, tmp_path, monkeypatch, fake_import_worker):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "v.wav"))
    editor._voice_row._on_import()
    assert editor._voice_row.state() == "importing"
    assert fake_import_worker.instances
    assert fake_import_worker.instances[0].source == str(tmp_path / "v.wav")


# -- User Story 5: loaded channel opens aimed adjustment panel -------------------

def test_loaded_music_channel_shows_name(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    assert instance._editor._music_row.state() == "loaded"


def test_loaded_music_click_opens_adjustment_aimed(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()

    instance._editor._on_channel_click("music")
    editor = instance._editor
    assert editor._panels.visible == panels.PANEL_ADJUSTMENT
    assert editor._panels.aim == "music"


def test_loaded_voice_click_aims_voice(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.voice = model_mod.MediaItem(kind="audio", filename="voice.wav")
    instance._editor.refresh()

    instance._editor._on_channel_click("voice")
    editor = instance._editor
    assert editor._panels.visible == panels.PANEL_ADJUSTMENT
    assert editor._panels.aim == "voice"


def test_reaim_keeps_panel_open(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance.project.movie.voice = model_mod.MediaItem(kind="audio", filename="voice.wav")
    instance._editor.refresh()

    editor = instance._editor
    editor._on_channel_click("music")
    editor._on_channel_click("voice")
    assert editor._panels.visible == panels.PANEL_ADJUSTMENT
    assert editor._panels.aim == "voice"


def test_loaded_but_missing_file_still_loaded(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="gone.mp3")
    instance._editor.refresh()
    assert instance._editor._music_row.state() == "loaded"


# -- User Story 6: transport states (Spec 5, FR-001/002/011) --------------------

def test_transport_unavailable_without_audio(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    t = instance._editor._transport
    assert t.state == "unavailable"
    assert str(t._button.cget("state")) == "disabled"
    assert t.tooltip() == transport.UNAVAILABLE_TOOLTIP


def test_transport_enabled_with_audio(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    root.update_idletasks()
    t = instance._editor._transport
    assert t.state == "play"
    assert str(t._button.cget("state")) == "normal"
    assert t.tooltip() == transport.PLAY_FROM_TOP_TOOLTIP


def test_transport_play_pause_preparing_rendering(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    from stillpoint.gui import icons

    t = instance._editor._transport

    t.set_state(transport.PLAY)
    assert str(t._button.cget("state")) == "normal"
    assert str(t._button.cget("image")) == str(icons.get_icon("play"))
    assert t.tooltip() == transport.PLAY_FROM_TOP_TOOLTIP

    t.set_state(transport.PAUSE)
    assert str(t._button.cget("state")) == "normal"
    assert str(t._button.cget("image")) == str(icons.get_icon("pause"))
    assert t.tooltip() == transport.PAUSE_TOOLTIP

    t.set_state(transport.PREPARING)
    assert str(t._button.cget("state")) == "disabled"
    assert str(t._button.cget("image")) == str(icons.get_icon("play", disabled=True))
    assert t.tooltip() == transport.PREPARING_TOOLTIP


def test_transport_resume_tooltip_when_paused(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    root.update_idletasks()
    t = instance._editor._transport
    t.set_state(transport.PLAY, paused=True)
    assert t.tooltip() == transport.RESUME_TOOLTIP


def test_export_shows_notice(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    instance._editor._on_export()
    assert noticed and "Exporting isn't ready yet" in noticed[0][1]


# -- User Story 6: volume slider in the adjustment panel (US2, FR-014/015) -----

def test_adjustment_panel_empty_aim_shows_plain_note(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    editor._on_rail_toggle(panels.PANEL_ADJUSTMENT)  # aims at empty music channel
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    assert panel._scale is None
    from stillpoint.gui import panels as panels_mod

    notes = [child.cget("text") for child in panel._sound_body.winfo_children()
             if child.winfo_class() == "Label"]
    assert panels_mod.EMPTY_AIM_NOTE in notes


def test_adjustment_panel_slider_initialized_from_stored_volume(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3", volume=0.6)
    instance._editor.refresh()
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    assert panel._scale is not None
    assert int(panel._scale.get()) == 60


def test_volume_slider_change_persists_balance(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3", volume=0.5)
    instance._editor.refresh()
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    panel._scale.set(25)
    panel._on_scale("25")  # what Tk invokes on a real drag
    assert instance.project.movie.audio.volume == pytest.approx(0.25)
    reloaded = model_mod.Project.load(tmp_path / "First Mix")
    assert reloaded.movie.audio.volume == pytest.approx(0.25)


def test_volume_slider_reaired_re_reads_volume(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3", volume=0.2)
    instance.project.movie.voice = model_mod.MediaItem(kind="audio", filename="v.wav", volume=0.8)
    instance._editor.refresh()
    editor = instance._editor

    editor._on_channel_click("music")
    root.update_idletasks()
    music_panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    assert int(music_panel._scale.get()) == 20

    editor._on_channel_click("voice")
    root.update_idletasks()
    voice_panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    assert voice_panel is music_panel  # one panel, one aimed channel
    assert int(voice_panel._scale.get()) == 80


def test_volume_slider_calls_editor_writer_not_panel(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3", volume=0.5)
    instance._editor.refresh()
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()

    writes = []
    monkeypatch.setattr(instance.project, "set_channel_setting",
                        lambda role, setting, value: writes.append((role, setting, value)))
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    panel._scale.set(40)
    panel._on_scale("40")
    assert writes == [("music", "volume", 0.4)]


def _load_music_with_file(instance, tmp_path, volume=0.5):
    """A loaded music channel whose file exists, so the mix signature reacts."""
    _open_empty_project(instance, tmp_path)
    project = instance.project
    media = project.media_dir()
    media.mkdir(parents=True, exist_ok=True)
    (media / "song.mp3").write_bytes(b"x")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3", volume=volume)
    project.save()
    instance._editor.refresh()
    return project


def test_volume_change_while_playing_rebakes_live(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    project = _load_music_with_file(instance, tmp_path)
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]

    editor._on_transport()  # play → bake → play
    assert len(fake_preview_worker) == 1
    session = editor._playback
    assert session.state == PlaybackSession.PLAYING

    session.sink._pos_seconds = 9.5
    panel._scale.set(25)
    panel._on_scale("25")  # the knob moves while previewing

    assert project.movie.audio.volume == pytest.approx(0.25)
    assert session.state == PlaybackSession.PLAYING  # old mix keeps playing
    assert len(fake_preview_worker) == 1  # debounced: no bake has started yet
    assert editor._rebake_id is not None  # a settled re-bake is pending

    editor._run_rebake()  # the debounce fires after the drag settles

    assert len(fake_preview_worker) == 2  # a live re-bake ran
    assert session.state == PlaybackSession.PLAYING
    assert session.sink.calls[-2] == ("open", str(fake_preview_worker[-1].out_path), 9.5)
    assert session.sink.calls[-1] == ("play",)


def test_volume_change_while_paused_rebakes_and_keeps_spot(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    project = _load_music_with_file(instance, tmp_path)
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]

    editor._on_transport()  # play
    editor._on_transport()  # pause partway
    session = editor._playback
    assert session.state == PlaybackSession.PAUSED
    session.sink._pos_seconds = 4.0

    panel._scale.set(10)
    panel._on_scale("10")
    editor._run_rebake()  # the debounce fires after the drag settles

    assert project.movie.audio.volume == pytest.approx(0.1)
    assert session.state == PlaybackSession.PLAYING  # live: re-baked and resumed
    assert len(fake_preview_worker) == 2
    assert session.sink.calls[-2] == ("open", str(fake_preview_worker[-1].out_path), 4.0)
    assert session.sink.calls[-1] == ("play",)


def test_volume_change_during_bake_never_starts_a_second(
    app, tmp_path, fake_session, monkeypatch
):
    """While a bake is in flight, slider changes persist but never start a
    second bake — the running bake sees the change via ``needs_rebake`` and
    re-bakes once it settles (FR-015; two ffmpeg bakes never contend,
    Constitution II)."""
    from stillpoint.gui import editor as editor_mod

    instances = []

    class DrainingWorker:
        def __init__(self, project, out_path, **kwargs):
            self.out_path = out_path
            instances.append(self)

        def start(self):
            pass

        def poll(self):
            return None  # the bake never completes during this test

    monkeypatch.setattr(editor_mod, "PreviewWorker", DrainingWorker)
    instance, root = app
    project = _load_music_with_file(instance, tmp_path)
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]

    editor._on_transport()  # play → bake (draining) → still baking
    assert editor._preview_worker is not None
    assert len(instances) == 1

    panel._scale.set(30)
    panel._on_scale("30")  # the knob moves while the bake runs
    panel._scale.set(40)
    panel._on_scale("40")

    assert project.movie.audio.volume == pytest.approx(0.4)  # persisted per tick
    assert len(instances) == 1  # never a second bake
    assert editor._rebake_id is None  # and nothing scheduled


def test_stub_actions_never_write_files(app, tmp_path, monkeypatch, fake_import_worker):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: None)
    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))

    before = {p.name for p in (tmp_path / "First Mix").iterdir()}
    editor._music_row._on_import()  # worker polls nothing → no file is written
    editor._on_export()
    editor._on_rail_toggle(panels.PANEL_IMAGE)
    editor._on_rail_toggle(panels.PANEL_IMAGE)
    editor._open_download_panel()

    after = {p.name for p in (tmp_path / "First Mix").iterdir()}
    assert before == after
    assert project.movie.audio is None and project.movie.voice is None


# -- User Story 3 (T025): idle CPU while paused ---------------------------------

def test_no_polling_while_paused(app, tmp_path, fake_session, fake_preview_worker):
    """Pausing cancels the pending playback poll: no after() callback is
    scheduled while paused, and invoking the poll handler stays a no-op
    (Constitution II, US-3 'idle CPU ≈ 0' acceptance)."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    editor = instance._editor

    editor._on_transport()  # bake → play
    root.update_idletasks()
    assert editor._playback.state == PlaybackSession.PLAYING
    assert editor._playback_poll_id is not None  # polling while playing

    editor._on_transport()  # pause
    root.update_idletasks()
    assert editor._playback.state == PlaybackSession.PAUSED
    assert editor._playback_poll_id is None  # nothing scheduled while paused

    editor._poll_playback()  # a stale/fired callback is a no-op
    assert editor._playback_poll_id is None  # and stays unscheduled
    assert fake_session.instances[0].sink.calls[-1] == ("pause",)


# -- User Story 4 (T029): Start over surface (FR-004, Clarification Q3) -----------

def test_start_over_hidden_until_playback_begins(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    root.update_idletasks()
    editor = instance._editor
    assert editor._transport.start_over_state() == "hidden"
    assert str(editor._transport._start_over.cget("state")) == "disabled"


def test_start_over_available_while_playing_and_paused(app, tmp_path, fake_session, fake_preview_worker):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    editor = instance._editor

    editor._on_transport()  # play
    root.update_idletasks()
    assert editor._transport.start_over_state() == "enabled"
    assert editor._transport._start_over.cget("text") == "Start over"

    editor._on_transport()  # pause partway
    root.update_idletasks()
    assert editor._transport.start_over_state() == "enabled"
    assert editor._transport.start_over_tooltip() == "Start the preview again from the top"


def test_start_over_disabled_while_baking(app, tmp_path, fake_session):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    editor = instance._editor
    from stillpoint.gui import editor as editor_mod
    from stillpoint.gui.workers import PreviewStatus

    class DrainingWorker:
        def __init__(self, project, out_path, **kwargs):
            pass

        def start(self):
            pass

        def poll(self):
            return None  # bake still running

    original = editor_mod.PreviewWorker
    editor_mod.PreviewWorker = DrainingWorker
    try:
        editor._on_transport()
    finally:
        editor_mod.PreviewWorker = original
    assert editor._transport.state == "preparing"
    assert editor._transport.start_over_state() == "hidden"


def test_no_position_bar_anywhere(app, tmp_path):
    """FR-004 / Clarification Q3: there is no position bar and nothing to drag."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    editor = instance._editor
    transport = editor._transport
    assert not hasattr(transport, "_seek")
    assert not hasattr(transport, "_position")
    assert not any(w.winfo_class() == "Scale" for w in transport.winfo_children())


# -- User Story 1 (006): the four-slider shaping panel (T005, T006) ------------


def test_adjustment_panel_renders_four_labeled_sliders_from_stored_values(app, tmp_path):
    """FR-002/FR-004/FR-009: a loaded channel shows exactly four sliders —
    Volume, Echo, Fade in, Fade out — each initialized from the stored value
    and each minimum stop labeled "Off"."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(
        kind="audio", filename="song.mp3",
        volume=0.6, echo=0.4, fade_in=2.0, fade_out=3.0,
    )
    instance._editor.refresh()
    editor = instance._editor
    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    from stillpoint.gui import panels as panels_mod

    assert set(panel._scales) == {"volume", "echo", "fade_in", "fade_out"}

    def _labels(widget):
        out = []
        for child in widget.winfo_children():
            if child.winfo_class() == "Label":
                out.append(child.cget("text"))
            out.extend(_labels(child))
        return out

    labels = _labels(panel._sound_body)
    for text in (panels_mod.VOLUME_LABEL, panels_mod.ECHO_LABEL,
                 panels_mod.FADE_IN_LABEL, panels_mod.FADE_OUT_LABEL):
        assert text in labels

    assert int(panel._scales["volume"].get()) == 60
    assert int(panel._scales["echo"].get()) == 40
    assert int(panel._scales["fade_in"].get()) == 20  # 2.0 s of the 10 s cap
    assert int(panel._scales["fade_out"].get()) == 30

    assert labels.count(panels_mod.OFF_LABEL) == 4  # every minimum stop is "Off"


def test_adjustment_panel_empty_aim_shows_only_plain_note(app, tmp_path):
    """FR-003: an empty aimed channel shows the plain line and no controls."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor
    editor._on_rail_toggle(panels.PANEL_ADJUSTMENT)
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    from stillpoint.gui import panels as panels_mod

    assert panels_mod.EMPTY_AIM_NOTE == "Add music or your voice first to shape its sound."
    assert panel._scales == {}
    assert panel._scale is None
    assert not any(w.winfo_class() == "Scale" for w in panel._sound_body.winfo_children())
    notes = [child.cget("text") for child in panel._sound_body.winfo_children()
             if child.winfo_class() == "Label"]
    assert notes == [panels_mod.EMPTY_AIM_NOTE]


def test_adjustment_panel_rereads_on_aim_and_project_change(app, tmp_path):
    """FR-003/FR-011: sliders re-read stored values on every aim change and
    on set_project."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(
        kind="audio", filename="song.mp3", volume=0.2, echo=0.1,
    )
    instance.project.movie.voice = model_mod.MediaItem(
        kind="audio", filename="v.wav", volume=0.8, echo=0.5, fade_in=1.0,
    )
    instance._editor.refresh()
    editor = instance._editor

    editor._on_channel_click("music")
    root.update_idletasks()
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    assert int(panel._scales["volume"].get()) == 20
    assert int(panel._scales["echo"].get()) == 10

    editor._on_channel_click("voice")
    root.update_idletasks()
    assert editor._panel_widgets[panels.PANEL_ADJUSTMENT] is panel  # one panel, re-aimed
    assert int(panel._scales["volume"].get()) == 80
    assert int(panel._scales["echo"].get()) == 50
    assert int(panel._scales["fade_in"].get()) == 10

    instance.project.movie.voice.echo = 0.9
    panel.set_project(instance.project)
    root.update_idletasks()
    assert int(panel._scales["echo"].get()) == 90


def test_slider_move_reports_on_setting_for_aimed_role(app, tmp_path, monkeypatch):
    """shaping-panel-ui.md: a slider move reports on_setting(role, setting,
    value) with the aimed role — 0..1 for volume/echo, seconds for fades."""
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(
        kind="audio", filename="song.mp3", volume=0.5)
    instance.project.movie.voice = model_mod.MediaItem(
        kind="audio", filename="v.wav", volume=0.5)
    instance._editor.refresh()
    editor = instance._editor
    editor._on_channel_click("voice")
    root.update_idletasks()

    writes = []
    monkeypatch.setattr(instance.project, "set_channel_setting",
                        lambda role, setting, value: writes.append((role, setting, value)))
    panel = editor._panel_widgets[panels.PANEL_ADJUSTMENT]
    panel._scales["volume"].set(60)
    panel._on_slider("volume", "60")
    panel._scales["echo"].set(35)
    panel._on_slider("echo", "35")
    panel._scales["fade_in"].set(70)
    panel._on_slider("fade_in", "70")
    assert writes == [
        ("voice", "volume", 0.6),
        ("voice", "echo", 0.35),
        ("voice", "fade_in", 7.0),
    ]

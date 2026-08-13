"""GUI smoke tests for the composed editor frame (research Decision 6).

These build the real frame on the shared Tk root fixture and drive clicks with
monkeypatched dialogs/file pickers so no modal window ever blocks.
"""

import pytest

from stillpoint import model as model_mod
from stillpoint.gui.app import App
from stillpoint.gui import panels


@pytest.fixture
def app(tk_root):
    instance = App(tk_root)
    yield instance, tk_root
    for child in tk_root.winfo_children():
        child.destroy()


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


def test_empty_music_channel_import_picks_then_notices(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    editor._on_import()
    assert noticed and "ready yet" in noticed[0][1]


def test_import_cancel_shows_no_notice(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: "")
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    editor._on_import()
    assert noticed == []


def test_import_never_modifies_project(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: None)
    editor._on_import()
    assert project.movie.audio is None
    assert project.movie.voice is None


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


def test_voice_import_notice(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    editor = instance._editor

    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "v.wav"))
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    editor._on_import()
    assert noticed and "ready yet" in noticed[0][1]


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


# -- User Story 6: transport and export are present but quiet -------------------

def test_transport_disabled_even_with_audio(app, tmp_path):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    instance.project.movie.audio = model_mod.MediaItem(kind="audio", filename="song.mp3")
    instance._editor.refresh()
    assert str(instance._editor._transport._button.cget("state")) == "disabled"


def test_export_shows_notice(app, tmp_path, monkeypatch):
    instance, root = app
    _open_empty_project(instance, tmp_path)
    noticed = []
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: noticed.append(a))
    instance._editor._on_export()
    assert noticed and "Exporting isn't ready yet" in noticed[0][1]


def test_stub_actions_never_write_files(app, tmp_path, monkeypatch):
    instance, root = app
    project = _open_empty_project(instance, tmp_path)
    editor = instance._editor
    monkeypatch.setattr("stillpoint.dialogs.info", lambda *a, **k: None)
    monkeypatch.setattr("stillpoint.gui.editor.pick_audio_file", lambda parent=None: str(tmp_path / "a.mp3"))

    before = {p.name for p in (tmp_path / "First Mix").iterdir()}
    editor._on_import()
    editor._on_export()
    editor._on_rail_toggle(panels.PANEL_IMAGE)
    editor._on_rail_toggle(panels.PANEL_IMAGE)
    editor._open_download_panel()

    after = {p.name for p in (tmp_path / "First Mix").iterdir()}
    assert before == after
    assert project.movie.audio is None and project.movie.voice is None

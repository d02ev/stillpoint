"""Headless unit tests for the editor frame's pure logic (research Decision 6).

These exercise PanelManager rules, channel-state derivation, and icon image
generation without any Tk display, so they are CI-safe.
"""

import pytest
from PIL import Image

from stillpoint import model as model_mod
from stillpoint.gui import icons
from stillpoint.gui.channels import MUSIC_ROLE, VOICE_ROLE, audio_display_name, channel_state
from stillpoint.gui.panels import (
    PANEL_ADJUSTMENT,
    PANEL_DOWNLOAD,
    PANEL_IMAGE,
    PanelManager,
)
from stillpoint.gui.transport import UNAVAILABLE_TOOLTIP, transport_available


def _movie(audio=None, voice=None) -> model_mod.Movie:
    return model_mod.Movie(audio=audio, voice=voice)


def _audio(filename: str) -> model_mod.MediaItem:
    return model_mod.MediaItem(kind="audio", filename=filename)


# -- channel-state derivation ----------------------------------------------

def test_channel_state_music_empty():
    assert channel_state(_movie(), MUSIC_ROLE) == ("empty", None)


def test_channel_state_voice_empty():
    assert channel_state(_movie(), VOICE_ROLE) == ("empty", None)


def test_channel_state_music_loaded():
    state, name = channel_state(_movie(audio=_audio("my-song.mp3")), MUSIC_ROLE)
    assert state == "loaded"
    assert name == "my-song.mp3"


def test_channel_state_voice_loaded():
    state, name = channel_state(_movie(voice=_audio("voice-over.wav")), VOICE_ROLE)
    assert state == "loaded"
    assert name == "voice-over.wav"


def test_channel_state_loaded_even_when_file_missing():
    # "Loaded" comes from the model record, never from the disk (spec Edge Cases).
    state, name = channel_state(_movie(audio=_audio("missing.mp3")), MUSIC_ROLE)
    assert state == "loaded"
    assert name == "missing.mp3"


def test_channel_state_unknown_role():
    with pytest.raises(ValueError):
        channel_state(_movie(), "banjo")


def test_audio_display_name_is_basename():
    assert audio_display_name(_audio("sub/folder/track.mp3")) == "track.mp3"
    assert audio_display_name(_audio("plain.wav")) == "plain.wav"


# -- PanelManager rules ------------------------------------------------------

def test_panel_manager_starts_closed():
    pm = PanelManager()
    assert pm.visible is None
    assert pm.aim == "music"


def test_open_shows_panel():
    pm = PanelManager()
    pm.open(PANEL_DOWNLOAD)
    assert pm.visible == PANEL_DOWNLOAD


def test_open_closes_previous_panel():
    pm = PanelManager()
    pm.open(PANEL_IMAGE)
    pm.open(PANEL_DOWNLOAD)
    assert pm.visible == PANEL_DOWNLOAD


def test_toggle_opens_closed_panel():
    pm = PanelManager()
    pm.toggle(PANEL_IMAGE)
    assert pm.visible == PANEL_IMAGE


def test_toggle_closes_open_panel():
    pm = PanelManager()
    pm.toggle(PANEL_IMAGE)
    pm.toggle(PANEL_IMAGE)
    assert pm.visible is None


def test_toggle_switches_panels():
    pm = PanelManager()
    pm.open(PANEL_IMAGE)
    pm.toggle(PANEL_DOWNLOAD)
    assert pm.visible == PANEL_DOWNLOAD


def test_aim_at_does_not_close_panel():
    pm = PanelManager()
    pm.open(PANEL_ADJUSTMENT)
    pm.aim_at(VOICE_ROLE)
    assert pm.visible == PANEL_ADJUSTMENT
    assert pm.aim == VOICE_ROLE


def test_reset_closes_and_resets_aim():
    pm = PanelManager()
    pm.open(PANEL_ADJUSTMENT)
    pm.aim_at(VOICE_ROLE)
    pm.reset()
    assert pm.visible is None
    assert pm.aim == "music"


# -- transport enablement (US1, FR-002) ----------------------------------------

def test_transport_unavailable_without_channels():
    assert transport_available(_movie()) is False


def test_transport_available_with_music():
    assert transport_available(_movie(audio=_audio("my-song.mp3"))) is True


def test_transport_available_with_voice():
    assert transport_available(_movie(voice=_audio("voice-over.wav"))) is True


def test_transport_available_with_both():
    assert transport_available(_movie(audio=_audio("a.mp3"), voice=_audio("v.wav"))) is True


def test_transport_available_even_when_file_missing():
    # "Recorded" comes from the model, never from the disk (FR-002).
    assert transport_available(_movie(audio=_audio("gone.mp3"))) is True


def test_transport_unavailable_tooltip_is_plain_language():
    assert UNAVAILABLE_TOOLTIP == "Add music or your voice to preview"


# -- Project.set_channel_volume (US2, FR-013/014/015) --------------------------

def test_set_channel_volume_round_trips(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.movie.voice = model_mod.MediaItem(kind="audio", filename="v.wav")
    project.set_channel_volume("music", 0.6)
    project.set_channel_volume("voice", 0.3)
    assert project.movie.audio.volume == 0.6
    assert project.movie.voice.volume == 0.3
    reloaded = model_mod.Project.load(tmp_path / "proj")
    assert reloaded.movie.audio.volume == 0.6
    assert reloaded.movie.voice.volume == 0.3


def test_set_channel_volume_persists_atomically(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.set_channel_volume("music", 0.8)
    # The write went through the existing atomic save path (project.json on disk).
    assert (tmp_path / "proj" / "project.json").is_file()


def test_set_channel_volume_clamps_to_unit_range(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.set_channel_volume("music", 1.7)
    assert project.movie.audio.volume == 1.0
    project.set_channel_volume("music", -0.2)
    assert project.movie.audio.volume == 0.0


def test_set_channel_volume_unknown_role_raises(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    with pytest.raises(ValueError):
        project.set_channel_volume("banjo", 0.5)


def test_set_channel_volume_unrecorded_channel_raises(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    with pytest.raises(ValueError):
        project.set_channel_volume("voice", 0.5)


def test_set_channel_volume_adds_no_schema_fields(tmp_path):
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.movie.voice = model_mod.MediaItem(kind="audio", filename="v.wav")
    project.set_channel_volume("music", 0.4)
    data = model_mod.Project.load(tmp_path / "proj").to_dict()
    movie = data["movie"]
    assert set(movie.keys()) == {"duration", "ratio", "crossfade", "audio", "voice"}
    assert set(movie["audio"].keys()) == {"kind", "filename", "duration", "in_point", "volume", "echo", "fade_in", "fade_out"}
    assert set(movie["voice"].keys()) == set(movie["audio"].keys())


# -- Project.set_channel_setting (006, US3: FR-004/010/011, Decision 5) ----------

def test_set_channel_setting_round_trips_every_setting(tmp_path):
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.movie.voice = model_mod.MediaItem(kind="audio", filename="v.wav")
    project.set_channel_setting("music", "volume", 0.6)
    project.set_channel_setting("music", "echo", 0.4)
    project.set_channel_setting("music", "fade_in", 2.0)
    project.set_channel_setting("voice", "fade_out", 3.0)
    assert project.movie.audio.volume == 0.6
    assert project.movie.audio.echo == 0.4
    assert project.movie.audio.fade_in == 2.0
    assert project.movie.voice.fade_out == 3.0
    reloaded = model_mod.Project.load(tmp_path / "proj")
    assert reloaded.movie.audio.echo == 0.4
    assert reloaded.movie.voice.fade_out == 3.0


def test_set_channel_setting_clamps_ranges(tmp_path):
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.set_channel_setting("music", "volume", 1.7)
    assert project.movie.audio.volume == 1.0
    project.set_channel_setting("music", "volume", -0.2)
    assert project.movie.audio.volume == 0.0
    project.set_channel_setting("music", "echo", 2.0)
    assert project.movie.audio.echo == 1.0
    project.set_channel_setting("music", "echo", -1.0)
    assert project.movie.audio.echo == 0.0
    project.set_channel_setting("music", "fade_in", 99.0)
    assert project.movie.audio.fade_in == model_mod.FADE_MAX_SECONDS
    project.set_channel_setting("music", "fade_in", -5.0)
    assert project.movie.audio.fade_in == 0.0


def test_set_channel_setting_unknown_setting_raises(tmp_path):
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    with pytest.raises(ValueError):
        project.set_channel_setting("music", "reverb", 0.5)


def test_set_channel_setting_unknown_role_raises(tmp_path):
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    with pytest.raises(ValueError):
        project.set_channel_setting("banjo", "volume", 0.5)


def test_set_channel_setting_unrecorded_channel_raises(tmp_path):
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    with pytest.raises(ValueError):
        project.set_channel_setting("voice", "echo", 0.5)


def test_set_channel_setting_never_writes_media(tmp_path):
    """FR-010: shaping writes only project.json — the media folder is untouched."""
    project = model_mod.new_project("Set", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    (project.media_dir() / "a.mp3").write_bytes(b"\x00" * 8)
    before = (project.media_dir() / "a.mp3").read_bytes()
    for setting, value in (("volume", 0.3), ("echo", 0.8), ("fade_in", 2.0), ("fade_out", 2.0)):
        project.set_channel_setting("music", setting, value)
    assert (project.media_dir() / "a.mp3").read_bytes() == before
    assert sorted(p.name for p in project.media_dir().iterdir()) == ["a.mp3"]


def test_set_channel_volume_alias_still_works(tmp_path):
    """Decision 5: the delivered one-line alias stays green."""
    project = model_mod.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model_mod.MediaItem(kind="audio", filename="a.mp3")
    project.set_channel_volume("music", 0.55)
    assert project.movie.audio.volume == pytest.approx(0.55)
    reloaded = model_mod.Project.load(tmp_path / "proj")
    assert reloaded.movie.audio.volume == pytest.approx(0.55)


# -- icon generation ---------------------------------------------------------

@pytest.mark.parametrize("name", ["picture", "download", "adjust", "import", "play", "pause", "export"])
def test_render_icon_produces_rgba_image(name):
    img = icons.render_icon(name, size=18)
    assert isinstance(img, Image.Image)
    assert img.size == (18, 18)
    assert img.mode == "RGBA"


def test_render_icon_disabled_variant():
    normal = icons.render_icon("play", size=18, disabled=False)
    disabled = icons.render_icon("play", size=18, disabled=True)
    assert normal.tobytes() != disabled.tobytes()


def test_render_icon_unknown_name():
    with pytest.raises(ValueError):
        icons.render_icon("mystery")

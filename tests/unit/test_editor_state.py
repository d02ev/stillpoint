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

import pytest

from stillpoint import model as model_mod
from stillpoint.gui.app import App


@pytest.fixture
def app(tk_root):
    instance = App(tk_root)
    yield instance, tk_root
    for child in tk_root.winfo_children():
        child.destroy()


def test_app_starts_on_home(app):
    instance, _root = app
    assert instance._current is instance._home
    assert instance.project is None


def test_create_project_switches_to_editor(app, tmp_path):
    instance, _root = app
    directory = tmp_path / "Zen Retreat"
    instance.create_project("Zen Retreat", directory)
    assert instance._current is instance._editor
    assert instance.project is not None
    assert instance.project.title == "Zen Retreat"
    assert directory.exists()


def test_open_project_switches_to_editor(app, tmp_path):
    instance, _root = app
    project = model_mod.new_project("Saved", tmp_path / "Saved", "t0")
    project.images.append(model_mod.MediaItem(kind="image", filename="a.jpg", duration=3.0))
    project.save()
    instance.open_project(tmp_path / "Saved")
    assert instance._current is instance._editor
    assert instance.project.title == "Saved"
    assert instance.project.images[0].duration == 3.0


def test_open_missing_project_shows_error(app, tmp_path, monkeypatch):
    instance, _root = app
    called = []
    monkeypatch.setattr("stillpoint.dialogs.error", lambda *a, **k: called.append(True))
    instance.open_project(tmp_path / "missing")
    assert called
    assert instance._current is instance._home


def test_show_home_clears_project(app, tmp_path):
    instance, _root = app
    instance.create_project("X", tmp_path / "X")
    instance.show_home()
    assert instance.project is None
    assert instance._current is instance._home


def test_editor_refresh_populates_controls(app, tmp_path):
    instance, _root = app
    project = model_mod.new_project("Ctrl", tmp_path / "Ctrl", "t0")
    project.movie.crossfade = 1.5
    project.save()
    instance.open_project(tmp_path / "Ctrl")
    editor = instance._editor
    assert editor._title_label.cget("text") == "Ctrl"
    assert editor._crossfade.get() == 1.5
    editor._crossfade.set(2.0)
    editor._on_crossfade()
    assert instance.project.movie.crossfade == 2.0

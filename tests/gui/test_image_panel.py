"""GUI smoke tests for the real image panel.

Built on the shared ``tk_root`` fixture with scripted fake workers (the
``test_editor_frame.py`` pattern) so no real network, thread, or dialog ever
runs. The panel's worker names are monkeypatched at module level.
"""

import os

import pytest
from PIL import Image

from stillpoint import model as model_mod, pexels
from stillpoint.gui import image_panel as image_panel_mod
from stillpoint.gui.workers import ImageDownloadEvent, PreviewEvent, SearchEvent


def _photo(photo_id=1, alt="A calm lake at sunset"):
    return pexels.Photo(
        id=photo_id,
        alt=alt,
        photographer="Jane Doe",
        width=4000,
        height=2250,
        base_url=f"https://images.pexels.com/photos/{photo_id}/pic.jpeg",
    )


def _thumb(color="red"):
    return Image.new("RGB", (480, 270), color)


def _write_image(path, color="red"):
    Image.new("RGB", (1920, 1080), color).save(path, "JPEG")


def _all_labels(widget):
    out = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            out.append(child.cget("text"))
        out.extend(_all_labels(child))
    return out


def _buttons(widget):
    out = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Button":
            out.append(child.cget("text"))
        out.extend(_buttons(child))
    return out


@pytest.fixture
def fake_workers(monkeypatch):
    """Replace image_panel's worker classes with scriptable fakes."""
    class FakeSearchWorker:
        script = []
        instances = []

        def __init__(self, query, key=None, *, fetch=None):
            self.query = query
            self.events = list(FakeSearchWorker.script)
            FakeSearchWorker.instances.append(self)

        def start(self):
            pass

        def poll(self):
            return self.events.pop(0) if self.events else None

    class FakeDownloadWorker:
        script = []
        instances = []

        def __init__(self, project, photo, key=None, *, fetch=None):
            self.project = project
            self.photo = photo
            self.events = list(FakeDownloadWorker.script)
            FakeDownloadWorker.instances.append(self)

        def start(self):
            pass

        def poll(self):
            return self.events.pop(0) if self.events else None

    class FakePreviewWorker:
        script = []
        instances = []

        def __init__(self, photo, *, fetch=None):
            self.photo = photo
            self.events = list(FakePreviewWorker.script)
            FakePreviewWorker.instances.append(self)

        def start(self):
            pass

        def poll(self):
            return self.events.pop(0) if self.events else None

    monkeypatch.setattr(image_panel_mod, "SearchWorker", FakeSearchWorker)
    monkeypatch.setattr(image_panel_mod, "ImageDownloadWorker", FakeDownloadWorker)
    monkeypatch.setattr(image_panel_mod, "PreviewImageWorker", FakePreviewWorker)
    yield FakeSearchWorker, FakeDownloadWorker, FakePreviewWorker
    for worker in (FakeSearchWorker, FakeDownloadWorker, FakePreviewWorker):
        worker.script = []
        worker.instances = []


@pytest.fixture
def panel(tk_root):
    instance = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    yield instance
    for child in tk_root.winfo_children():
        child.destroy()


# -- User Story 1 (T008): search renders results -------------------------------


def test_search_renders_result_rows_with_16_9_thumbs_and_two_buttons(panel, fake_workers, tk_root):
    fake_workers[0].script = [
        SearchEvent("done", photos=[_photo(1), _photo(2)], thumbs={1: _thumb(), 2: _thumb("blue")}, detail=""),
    ]
    panel._search_var.set("lake")
    panel._on_search()
    tk_root.update_idletasks()

    assert len(fake_workers[0].instances) == 1
    assert fake_workers[0].instances[0].query == "lake"
    assert len(panel._result_rows) == 2
    for row in panel._result_rows:
        assert _buttons(row) == ["Preview", "Download"]
        image_labels = [
            w for w in row.winfo_children() if w.winfo_class() == "Label" and w.cget("image")
        ]
        assert image_labels, "each result row shows a thumbnail image"
        img = image_labels[0].image
        assert abs(img.width() / img.height() - 16 / 9) < 0.01


def test_blank_query_shows_placeholder_and_searches_nothing(panel, fake_workers, tk_root):
    panel._search_var.set("   ")
    panel._on_search()
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.SEARCH_PLACEHOLDER
    assert fake_workers[0].instances == []
    assert panel._result_rows == []


def test_no_results_event_shows_plain_message(panel, fake_workers, tk_root):
    fake_workers[0].script = [
        SearchEvent("empty", photos=[], thumbs={}, detail=pexels.NO_RESULTS_MESSAGE),
    ]
    panel._search_var.set("nope")
    panel._on_search()
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.NO_RESULTS_MESSAGE
    assert panel._result_rows == []
    assert panel._busy is False


def test_second_search_while_searching_shows_wait_and_no_second_worker(panel, fake_workers, tk_root):
    fake_workers[0].script = []  # drains forever: the first job never completes
    panel._search_var.set("lake")
    panel._on_search()
    panel._search_var.set("sky")
    panel._on_search()
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.WAIT_FOR_JOB_MESSAGE
    assert len(fake_workers[0].instances) == 1


# -- User Story 2 (T014): download flow ----------------------------------------


def test_download_done_updates_status_library_and_calls_on_choose(panel, fake_workers, tk_root, tmp_path):
    project = model_mod.new_project("Proj", tmp_path / "Proj", "t0")
    (project.media_dir() / "lake.jpg").write_bytes(b"x")
    panel.set_project(project)
    chosen = []
    panel._on_choose = lambda name: chosen.append(name)

    fake_workers[1].script = [
        ImageDownloadEvent("downloading", value="", detail=pexels.DOWNLOADING_MESSAGE),
        ImageDownloadEvent("done", value="lake.jpg", detail=pexels.DOWNLOAD_DONE_MESSAGE),
    ]
    panel._on_download(_photo())
    tk_root.update_idletasks()

    assert panel._status.cget("text") == pexels.DOWNLOAD_DONE_MESSAGE
    assert chosen == ["lake.jpg"]
    assert "lake.jpg" in _all_labels(panel._library)


def test_download_start_shows_downloading_message(panel, fake_workers, tk_root):
    fake_workers[1].script = [
        ImageDownloadEvent("downloading", value="", detail=pexels.DOWNLOADING_MESSAGE),
    ]
    panel._on_download(_photo())
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.DOWNLOADING_MESSAGE
    assert panel._busy is True


def test_second_download_while_busy_shows_wait_and_no_second_worker(panel, fake_workers, tk_root):
    fake_workers[1].script = []
    panel._on_download(_photo(1))
    panel._on_download(_photo(2))
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.WAIT_FOR_JOB_MESSAGE
    assert len(fake_workers[1].instances) == 1


def test_download_error_shows_classified_message_and_project_untouched(panel, fake_workers, tk_root, tmp_path):
    project = model_mod.new_project("Proj", tmp_path / "Proj", "t0")
    panel.set_project(project)
    before = sorted(p.name for p in project.media_dir().iterdir())

    fake_workers[1].script = [
        ImageDownloadEvent("error", value="", detail=pexels.DOWNLOAD_ERROR_NO_CONNECTION),
    ]
    panel._on_download(_photo())
    tk_root.update_idletasks()

    assert panel._status.cget("text") == pexels.DOWNLOAD_ERROR_NO_CONNECTION
    assert panel._busy is False
    assert sorted(p.name for p in project.media_dir().iterdir()) == before
    assert str(panel._search_btn.cget("state")) == "normal"


# -- User Story 3 (T022): persistence ------------------------------------------


def test_set_project_marks_current_background(tmp_path, tk_root):
    project = model_mod.new_project("Lib", tmp_path / "Lib", "t0")
    (project.media_dir() / "a.jpg").write_bytes(b"x")
    (project.media_dir() / "b.jpg").write_bytes(b"x")
    project.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0)]
    project.save()

    panel = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    panel.set_project(project)
    tk_root.update_idletasks()

    labels = _all_labels(panel._library)
    assert "a.jpg" + pexels.BACKGROUND_MARKER in labels
    assert "b.jpg" + pexels.BACKGROUND_MARKER not in labels
    for child in tk_root.winfo_children():
        child.destroy()


def test_set_project_pre007_opens_with_empty_message(tmp_path, tk_root):
    project = model_mod.new_project("Old", tmp_path / "Old", "t0")

    panel = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    panel.set_project(project)
    tk_root.update_idletasks()

    assert pexels.LIBRARY_EMPTY_MESSAGE in _all_labels(panel._library)
    for child in tk_root.winfo_children():
        child.destroy()


def test_set_project_missing_background_file_opens_safely(tmp_path, tk_root):
    project = model_mod.new_project("Gap", tmp_path / "Gap", "t0")
    project.images = [model_mod.MediaItem(kind="image", filename="gone.jpg", duration=5.0)]
    project.save()

    panel = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    panel.set_project(project)  # must not crash
    tk_root.update_idletasks()
    assert panel._project is project
    assert panel._current_image_ref is None  # missing file → plain unset card
    assert pexels.CURRENT_PICTURE_EMPTY in _all_labels(panel)
    for child in tk_root.winfo_children():
        child.destroy()


# -- User Story 4 (T027): no connection ----------------------------------------


def test_search_error_shows_classified_message_and_ready_to_retry(panel, fake_workers, tk_root):
    fake_workers[0].script = [
        SearchEvent("error", photos=[], thumbs={}, detail=pexels.SEARCH_ERROR_NO_CONNECTION),
    ]
    panel._search_var.set("lake")
    panel._on_search()
    tk_root.update_idletasks()
    assert panel._status.cget("text") == pexels.SEARCH_ERROR_NO_CONNECTION
    assert panel._busy is False
    assert str(panel._search_btn.cget("state")) == "normal"


# -- User Story 5 (T031): library swap -----------------------------------------


def test_library_rows_newest_first_and_click_switches(panel, tk_root, tmp_path):
    project = model_mod.new_project("Lib", tmp_path / "Lib", "t0")
    media = project.media_dir()
    (media / "a.jpg").write_bytes(b"x")
    (media / "b.jpg").write_bytes(b"x")
    os.utime(media / "a.jpg", (100, 100))
    os.utime(media / "b.jpg", (200, 200))
    project.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0)]
    project.save()

    chosen = []

    def _choose(name):
        project.set_background_image(name)
        chosen.append(name)

    panel._on_choose = _choose
    panel.set_project(project)
    tk_root.update_idletasks()

    assert [name for name, _row in panel._library_rows] == ["b.jpg", "a.jpg"]
    assert "a.jpg" + pexels.BACKGROUND_MARKER in _all_labels(panel._library)

    panel._on_library_click("b.jpg")
    tk_root.update_idletasks()
    assert chosen == ["b.jpg"]
    assert "b.jpg" + pexels.BACKGROUND_MARKER in _all_labels(panel._library)
    assert "a.jpg" + pexels.BACKGROUND_MARKER not in _all_labels(panel._library)


def test_clicking_current_background_is_noop(panel, tk_root, tmp_path):
    project = model_mod.new_project("Lib", tmp_path / "Lib", "t0")
    (project.media_dir() / "a.jpg").write_bytes(b"x")
    project.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0)]
    project.save()

    chosen = []
    panel._on_choose = lambda name: chosen.append(name)
    panel.set_project(project)
    tk_root.update_idletasks()

    panel._on_library_click("a.jpg")
    assert chosen == []


# -- Current picture card ------------------------------------------------------


def test_set_project_shows_current_picture_card(tmp_path, tk_root):
    project = model_mod.new_project("Card", tmp_path / "Card", "t0")
    _write_image(project.media_dir() / "a.jpg")
    project.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0)]
    project.save()

    panel = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    panel.set_project(project)
    tk_root.update_idletasks()

    assert panel._current_image_ref is not None
    assert abs(panel._current_image_ref.width() / panel._current_image_ref.height() - 16 / 9) < 0.01
    assert pexels.CURRENT_PICTURE_EMPTY not in _all_labels(panel)
    for child in tk_root.winfo_children():
        child.destroy()


def test_set_project_no_background_shows_empty_card(tmp_path, tk_root):
    project = model_mod.new_project("Empty", tmp_path / "Empty", "t0")

    panel = image_panel_mod.ImagePanel(tk_root, on_choose=None)
    panel.set_project(project)
    tk_root.update_idletasks()

    assert panel._current_image_ref is None
    assert pexels.CURRENT_PICTURE_EMPTY in _all_labels(panel)
    for child in tk_root.winfo_children():
        child.destroy()


def test_library_click_updates_current_picture_card(panel, tk_root, tmp_path):
    project = model_mod.new_project("Swap", tmp_path / "Swap", "t0")
    media = project.media_dir()
    _write_image(media / "a.jpg", color="red")
    _write_image(media / "b.jpg", color="blue")
    os.utime(media / "a.jpg", (100, 100))
    os.utime(media / "b.jpg", (200, 200))
    project.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0)]
    project.save()

    chosen = []

    def _choose(name):
        project.set_background_image(name)
        chosen.append(name)

    panel._on_choose = _choose
    panel.set_project(project)
    tk_root.update_idletasks()

    first = panel._current_image_ref
    panel._on_library_click("b.jpg")
    tk_root.update_idletasks()

    assert chosen == ["b.jpg"]
    assert panel._current_image_ref is not None
    assert panel._current_image_ref is not first  # the card repainted the new picture
    assert panel._current_item().filename == "b.jpg"


# -- User Story 6 (T034): gentle machine ---------------------------------------


def test_no_polling_after_job_completes(panel, fake_workers, tk_root):
    fake_workers[0].script = [
        SearchEvent("done", photos=[], thumbs={}, detail=""),
    ]
    panel._search_var.set("lake")
    panel._on_search()
    tk_root.update_idletasks()

    assert panel._busy is False
    assert panel._poll_id is None
    panel._poll()  # a stale poll handler is a no-op
    assert panel._poll_id is None


# -- User Story 2 (T020): the preview pop-out -----------------------------------


def test_preview_shown_opens_nonmodal_toplevel_with_image(panel, fake_workers, tk_root):
    fake_workers[2].script = [
        PreviewEvent("shown", value=_thumb("green"), detail=""),
    ]
    panel._on_preview(_photo())
    tk_root.update_idletasks()

    assert panel._preview_toplevel is not None
    assert panel._preview_toplevel.winfo_exists()
    image_label = _find_image_label(panel._preview_toplevel)
    assert image_label is not None
    assert panel._preview_worker is None
    assert panel._preview_poll_id is None


def test_preview_error_shows_plain_message_in_popout(panel, fake_workers, tk_root):
    fake_workers[2].script = [
        PreviewEvent("error", value=None, detail=pexels.PREVIEW_ERROR_MESSAGE),
    ]
    panel._on_preview(_photo())
    tk_root.update_idletasks()

    assert panel._preview_toplevel is not None
    assert pexels.PREVIEW_ERROR_MESSAGE in _all_labels(panel._preview_toplevel)
    assert panel._preview_worker is None
    assert panel._preview_poll_id is None


def _find_image_label(widget):
    for child in widget.winfo_children():
        if child.winfo_class() == "Label" and child.cget("image"):
            return child
        found = _find_image_label(child)
        if found:
            return found
    return None

import pytest
from PIL import Image

from stillpoint import media, model as model_mod
from stillpoint.gui import preview, timeline, waveform
from stillpoint.gui.workers import RenderWorker


@pytest.fixture
def project_with_image(tmp_path):
    proj = model_mod.new_project("Gui Media", tmp_path / "proj", "t0")
    src = tmp_path / "photo.png"
    Image.new("RGB", (320, 180), (90, 160, 90)).save(src)
    proj.add_image(src)
    proj.save()
    return proj


@pytest.fixture
def root(tk_root):
    for child in tk_root.winfo_children():
        child.destroy()
    return tk_root


def test_timeline_draws_thumbnail(root, project_with_image):
    widget = timeline.Timeline(root)
    widget.pack()
    widget.set_project(project_with_image, selected=0)
    root.update_idletasks()
    assert widget.selected_index() == 0
    assert len(widget._thumbs) == 1


def test_timeline_select_roundtrip(root, project_with_image):
    widget = timeline.Timeline(root)
    widget.set_project(project_with_image)
    widget.select(0)
    assert widget.selected_index() == 0


def test_preview_draws_without_crashing(root, project_with_image):
    widget = preview.Preview(root)
    widget.pack(fill="both", expand=True)
    widget.set_project(project_with_image)
    widget.show_index(0)
    root.update_idletasks()
    assert widget._image_ref is not None


def test_waveform_draws_peaks(root):
    widget = waveform.Waveform(root)
    widget.pack()
    widget.set_peaks([0.1, 0.5, 0.9, 0.2])
    root.update_idletasks()
    assert widget._peaks == [0.1, 0.5, 0.9, 0.2]


def test_render_worker_reports_done(tmp_path):
    proj = model_mod.new_project("Worker", tmp_path / "proj", "t0")
    src = tmp_path / "img.png"
    Image.new("RGB", (160, 90), (200, 120, 40)).save(src)
    item = proj.add_image(src)
    item.duration = 1.0
    proj.save()
    out = tmp_path / "out.mp4"
    worker = RenderWorker(proj, out)
    worker.start()
    import time

    deadline = time.time() + 60
    statuses = []
    while time.time() < deadline:
        status = worker.poll()
        if status:
            statuses.append(status.state)
            if status.state in ("done", "error"):
                break
        time.sleep(0.05)
    assert statuses[-1] == "done"
    assert out.is_file()

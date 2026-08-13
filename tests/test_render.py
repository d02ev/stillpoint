import subprocess

import pytest
from PIL import Image

from stillpoint import media, model as model_mod, render


def _make_image(path, size=(320, 180), color=(120, 60, 200)):
    Image.new("RGB", size, color).save(path)


def _make_tone(path, seconds=3.0):
    subprocess.run(
        [str(media.ffmpeg_path()), "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=300:duration={seconds}", "-af", "volume=6.0", "-y", str(path)],
        check=True,
    )


def _project_with_images(tmp_path, count=2, duration=1.0):
    proj = model_mod.new_project("Smoke", tmp_path / "proj", "t0", ratio=model_mod.RATIO_WIDE)
    for i in range(count):
        src = tmp_path / f"img{i}.png"
        _make_image(src)
        item = proj.add_image(src)
        item.duration = duration
    proj.save()
    return proj


def _probe_duration(path):
    return media.probe_duration(path)


def test_timeline_duration_counts_crossfade_overlap():
    proj = model_mod.Project(title="T", directory=None)
    proj.images = [model_mod.MediaItem(kind="image", filename="a.jpg", duration=5.0),
                   model_mod.MediaItem(kind="image", filename="b.jpg", duration=5.0)]
    proj.movie.crossfade = 1.0
    assert render.timeline_duration(proj) == 9.0
    proj.movie.crossfade = 0.0
    assert render.timeline_duration(proj) == 10.0


def test_build_spec_single_image(tmp_path):
    proj = _project_with_images(tmp_path, count=1, duration=2.0)
    spec = render.build_spec(proj, tmp_path / "out.mp4")
    assert spec.total == 2.0
    assert "-loop" in spec.inputs
    assert "xfade" not in spec.filter_complex
    assert "vout" in spec.filter_complex
    assert not spec.has_audio


def test_build_spec_crossfade_chains(tmp_path):
    proj = _project_with_images(tmp_path, count=3, duration=2.0)
    proj.movie.crossfade = 0.5
    proj.save()
    spec = render.build_spec(proj, tmp_path / "out.mp4")
    assert spec.total == 2.0 * 3 - 0.5 * 2
    assert spec.filter_complex.count("xfade=") == 2


def test_render_single_image(tmp_path):
    proj = _project_with_images(tmp_path, count=1, duration=1.0)
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    assert out.is_file() and out.stat().st_size > 1000
    assert _probe_duration(out) == pytest.approx(1.0, abs=0.1)


def test_render_two_images_no_crossfade(tmp_path):
    proj = _project_with_images(tmp_path, count=2, duration=1.0)
    proj.movie.crossfade = 0.0
    proj.save()
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    assert _probe_duration(out) == pytest.approx(2.0, abs=0.1)


def test_render_with_crossfade_and_audio(tmp_path):
    proj = _project_with_images(tmp_path, count=2, duration=1.0)
    proj.movie.crossfade = 0.5
    tone = tmp_path / "tone.wav"
    _make_tone(tone)
    proj.movie.audio = model_mod.MediaItem(kind="audio", filename="tone.wav", in_point=0.0, volume=0.8, fade_in=0.2, fade_out=0.2)
    (proj.media_dir() / "tone.wav").write_bytes(tone.read_bytes())
    proj.save()
    out = tmp_path / "out.mp4"
    progress = []
    render.render_with_progress(proj, out, progress.append)
    assert _probe_duration(out) == pytest.approx(1.5, abs=0.1)
    assert progress and progress[-1] == 1.0


def test_render_solid_background_when_no_images(tmp_path):
    proj = model_mod.new_project("Empty", tmp_path / "proj", "t0")
    proj.movie.duration = 1.0
    proj.save()
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    assert out.is_file()
    assert _probe_duration(out) == 1.0


def test_render_output_path_unique(tmp_path):
    proj = model_mod.new_project("Unique", tmp_path / "proj", "t0")
    first = render.render_output_path(proj)
    first.touch()
    second = render.render_output_path(proj)
    assert first != second

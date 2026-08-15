import re
import subprocess

import pytest
from PIL import Image

from stillpoint import media, mix, model as model_mod, render


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
    proj.movie.duration = 2.0  # the single still fills the whole film (007)
    proj.save()
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
    proj.movie.duration = 1.0  # the single still fills the whole film (007)
    proj.save()
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


# -- US2: the keystone — preview and export are one mix (FR-006/007) ----------


def _attach(proj, role, tmp_path, spec):
    """Put a tone file on disk as the channel's source and record it."""
    tone = tmp_path / f"{role}.wav"
    seconds = 0.8 if spec.get("short") else 5.0
    _make_tone(tone, seconds=seconds)
    (proj.media_dir() / f"{role}.wav").write_bytes(tone.read_bytes())
    kwargs = {k: v for k, v in spec.items() if k != "short"}
    item = model_mod.MediaItem(kind="audio", filename=f"{role}.wav", **kwargs)
    setattr(proj.movie, "audio" if role == "music" else "voice", item)


def _extract_audio_to_wav(mp4, wav):
    subprocess.run(
        [str(media.ffmpeg_path()), "-v", "error", "-i", str(mp4), "-vn",
         "-ac", "2", "-ar", "44100", "-y", str(wav)],
        check=True,
    )


def _mean_volume(wav):
    result = subprocess.run(
        [str(media.ffmpeg_path()), "-i", str(wav), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", result.stderr)
    return float(match.group(1)) if match else None


def test_build_spec_composes_the_shared_mix(tmp_path):
    proj = _project_with_images(tmp_path, count=2, duration=1.0)
    proj.movie.crossfade = 0.5
    _attach(proj, "music", tmp_path, {"volume": 0.7})
    _attach(proj, "voice", tmp_path, {"volume": 0.3})
    proj.save()
    spec = render.build_spec(proj, tmp_path / "out.mp4")
    assert spec.has_audio is True
    assert "amix=inputs=2:normalize=0[aout]" in spec.filter_complex


def test_render_both_channels_matches_timeline(tmp_path):
    proj = _project_with_images(tmp_path, count=2, duration=1.0)
    proj.movie.crossfade = 0.5
    _attach(proj, "music", tmp_path, {"volume": 0.7})
    _attach(proj, "voice", tmp_path, {"volume": 0.3})
    proj.save()
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    assert _probe_duration(out) == pytest.approx(1.5, abs=0.1)


@pytest.mark.parametrize(
    "music,voice",
    [
        pytest.param({"volume": 0.7, "fade_in": 0.1, "fade_out": 0.1}, {"volume": 0.3, "short": True}, id="both-short-voice"),
        pytest.param({"volume": 0.5, "short": True}, {"volume": 0.9, "fade_in": 0.2}, id="both-short-music"),
        pytest.param({"volume": 0.6}, None, id="music-only"),
        pytest.param(None, {"volume": 0.4}, id="voice-only"),
        pytest.param({"volume": 0.5, "echo": 0.3}, None, id="music-echo-only"),
        pytest.param(None, {"volume": 0.6, "echo": 0.8, "fade_in": 0.1}, id="voice-echo-and-fade"),
        pytest.param({"volume": 0.4, "echo": 0.7}, {"volume": 0.8, "echo": 0.2}, id="both-echo"),
        pytest.param({"volume": 0.5, "echo": 1.0}, {"volume": 0.5, "echo": 0.0}, id="echo-extremes"),
    ],
)
def test_preview_and_export_same_mix_parity(tmp_path, music, voice):
    """The preview WAV and the exported mp4 audio are the same mix (FR-006/007).

    Same channels, same balance (volume, echo, fades), same timing, same length
    — because both are built from ``mix.plan_audio``. Echo is part of the
    shared chain, so what she hears with echo on is exactly what exports
    (US4/FR-014; Scenario 4 covers states echo on/off, extremes, one or both).
    """
    proj = _project_with_images(tmp_path, count=2, duration=1.0)
    proj.movie.crossfade = 0.5
    if music:
        _attach(proj, "music", tmp_path, music)
    if voice:
        _attach(proj, "voice", tmp_path, voice)
    proj.save()

    preview = tmp_path / "preview.wav"
    mix.render_mix(proj, preview)
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    extracted = tmp_path / "out.wav"
    _extract_audio_to_wav(out, extracted)

    preview_dur = media.probe_duration(preview)
    exported_dur = media.probe_duration(extracted)
    assert preview_dur == pytest.approx(exported_dur, abs=0.2)

    preview_loud = _mean_volume(preview)
    exported_loud = _mean_volume(extracted)
    assert preview_loud is not None and exported_loud is not None
    assert preview_loud == pytest.approx(exported_loud, abs=1.0)


def test_missing_channel_is_silence_present_channel_plays(tmp_path):
    """A recorded-but-missing channel becomes silence; the other still plays (FR-010)."""
    proj = _project_with_images(tmp_path, count=1, duration=1.0)
    proj.movie.duration = 1.0  # the single still fills the whole film (007)
    _attach(proj, "music", tmp_path, {"volume": 0.7})
    proj.movie.voice = model_mod.MediaItem(kind="audio", filename="gone.wav", volume=0.8)
    proj.save()
    out = tmp_path / "out.mp4"
    render.render(proj, out)
    extracted = tmp_path / "out.wav"
    _extract_audio_to_wav(out, extracted)
    assert _probe_duration(extracted) == pytest.approx(1.0, abs=0.2)

    baseline = _project_with_images(tmp_path, count=1, duration=1.0)
    baseline.movie.duration = 1.0  # the single still fills the whole film (007)
    _attach(baseline, "music", tmp_path, {"volume": 0.7})
    baseline.save()
    baseline_mp4 = tmp_path / "baseline.mp4"
    render.render(baseline, baseline_mp4)
    baseline_wav = tmp_path / "baseline.wav"
    _extract_audio_to_wav(baseline_mp4, baseline_wav)
    assert _mean_volume(extracted) == pytest.approx(_mean_volume(baseline_wav), abs=1.0)

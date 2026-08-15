"""T009/T003–T005: the shared mix path.

Covers plan_audio (channels, missing-file skip, indices, chain shape),
mix_signature (bake caching), and render_mix (silent fallback + real ffmpeg
bake + failure). Headless — no Tk.
"""

import struct
import subprocess
import wave

import pytest

from stillpoint import media, mix, model as model_mod


def _make_tone(path, seconds: float = 2.0) -> None:
    subprocess.run(
        [str(media.ffmpeg_path()), "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=300:duration={seconds}",
         "-af", "volume=6.0", "-y", str(path)],
        check=True,
    )


def _make_image(path, size=(320, 180), color=(120, 60, 200)) -> None:
    from PIL import Image

    Image.new("RGB", size, color).save(path)


def _project(tmp_path, *, images=1, duration=1.0) -> model_mod.Project:
    proj = model_mod.new_project("Mix", tmp_path / "proj", "t0", ratio=model_mod.RATIO_WIDE)
    for i in range(images):
        src = tmp_path / f"img{i}.png"
        _make_image(src)
        item = proj.add_image(src)
        item.duration = duration
    proj.save()
    return proj


def _attach_tone(proj, tmp_path, role, seconds=2.0, **kw) -> model_mod.MediaItem:
    tone = tmp_path / f"{role}.wav"
    _make_tone(tone, seconds)
    item = model_mod.MediaItem(kind="audio", filename=f"{role}.wav", **kw)
    (proj.media_dir() / f"{role}.wav").write_bytes(tone.read_bytes())
    setattr(proj.movie, "audio" if role == "music" else "voice", item)
    proj.save()
    return item


# -- plan_audio ---------------------------------------------------------------


def test_plan_no_audio_is_silence(tmp_path):
    proj = _project(tmp_path)
    plan = mix.plan_audio(proj, total=5.0)
    assert plan.inputs == []
    assert plan.chains == []
    assert plan.has_audio is False
    assert plan.total == 5.0


def test_plan_single_channel_labels_aout(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5)
    plan = mix.plan_audio(proj, total=5.0)
    assert plan.has_audio is True
    assert len(plan.inputs) == 2  # ["-i", path]
    assert len(plan.chains) == 1
    assert plan.chains[0].startswith("[0:a]")
    assert plan.chains[0].endswith("[aout]")
    assert "amix" not in " ".join(plan.chains)


def test_plan_two_channels_mix_normalize_0(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.8)
    _attach_tone(proj, tmp_path, "voice", volume=0.4)
    plan = mix.plan_audio(proj, total=5.0)
    assert len(plan.inputs) == 4
    assert len(plan.chains) == 3
    assert "[c0][c1]amix=inputs=2:normalize=0[aout]" in plan.chains
    assert plan.chains[0].endswith("[c0]")
    assert plan.chains[1].endswith("[c1]")


def test_plan_skips_recorded_but_missing_file(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music")
    proj.movie.audio.filename = "gone.wav"  # recorded but file gone (FR-010)
    proj.save()
    plan = mix.plan_audio(proj, total=5.0)
    assert plan.inputs == []
    assert plan.has_audio is False


def test_plan_respects_balance_fades_and_trim(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", in_point=0.3, volume=0.6, fade_in=0.2, fade_out=0.4)
    plan = mix.plan_audio(proj, total=5.0)
    chain = plan.chains[0]
    assert "atrim=start=0.300:end=5.300" in chain
    assert "volume=0.600" in chain
    assert "afade=t=in:st=0:d=0.200" in chain
    assert "afade=t=out:st=4.600:d=0.400" in chain
    assert "apad" in chain


def test_plan_index_offset_shifts_inputs(tmp_path):
    proj = _project(tmp_path, images=2)
    _attach_tone(proj, tmp_path, "music")
    plan = mix.plan_audio(proj, total=5.0, index_offset=2)
    assert plan.chains[0].startswith("[2:a]")


def test_plan_total_matches_timeline(tmp_path):
    proj = _project(tmp_path, images=2)
    proj.movie.crossfade = 0.5
    proj.save()
    plan = mix.plan_audio(proj, total=mix.timeline_duration(proj))
    assert plan.total == pytest.approx(1.5)


# -- T010/T011 (006): echo in the shared chain and signature ----------------------


def _echo_decay(chain: str) -> float:
    """Extract the aecho decay from a chain fragment ('' when none)."""
    import re

    match = re.search(r"aecho=1\.0:([\d.]+):350:\1", chain)
    if not match:
        return None
    return float(match.group(1))


def test_plan_echo_off_is_byte_for_byte_the_spec5_chain(tmp_path):
    """Echo off (0.0) emits no aecho stage — the chain is the Spec 5 chain."""
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", in_point=0.3, volume=0.6, fade_in=0.2, fade_out=0.4)
    plan = mix.plan_audio(proj, total=5.0)
    chain = plan.chains[0]
    assert "aecho" not in chain
    assert chain == (
        "[0:a]"
        "atrim=start=0.300:end=5.300,"
        "asetpts=PTS-STARTPTS,"
        "volume=0.600,"
        "afade=t=in:st=0:d=0.200,"
        "afade=t=out:st=4.600:d=0.400,"
        "apad,atrim=0:5.000[aout]"
    )


def test_plan_echo_toggle_is_byte_reversible(tmp_path):
    """Scenario 3.1/3.2: turning echo Off returns the exact un-echoed chain, and
    the same on-position reproduces the same chain — reversible, never reset."""
    def chain_for(echo):
        proj = _project(tmp_path)
        _attach_tone(proj, tmp_path, "music", volume=0.5, echo=echo)
        return mix.plan_audio(proj, total=5.0).chains[0]

    assert chain_for(0.0) == chain_for(0.0)
    assert chain_for(0.6) == chain_for(0.6)  # same position, same sound
    assert chain_for(0.0) != chain_for(0.6)
    assert "aecho" not in chain_for(0.0)


def test_plan_echo_with_fade_exceeding_timeline(tmp_path):
    """Scenario 6.4: a fade longer than the timeline never errors — the echo
    stage and fades are still emitted in order; ffmpeg clips the fade to the
    sound that is heard, identically in preview and export."""
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.6, echo=0.9, fade_out=8.0)
    plan = mix.plan_audio(proj, total=5.0)
    chain = plan.chains[0]
    assert chain.count("aecho") == 1
    assert chain.index("volume=0.600") < chain.index("aecho") < chain.index("afade=t=out")
    assert "apad,atrim=0:5.000" in chain


def test_plan_echo_on_places_single_aecho_between_volume_and_fades(tmp_path):
    """Echo on emits exactly one mapped aecho stage, between volume and fades."""
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5, echo=0.5, fade_in=0.2, fade_out=0.4)
    plan = mix.plan_audio(proj, total=5.0)
    chain = plan.chains[0]
    decay = min(0.5 * 0.6, 0.7)  # clamp(0.5 * 0.6, 0.0, 0.7) == 0.3
    assert chain.count("aecho") == 1
    assert f"aecho=1.0:{decay:.3f}:350:{decay:.3f}" in chain
    assert chain.index("volume=0.500") < chain.index("aecho") < chain.index("afade=t=in")


def test_plan_echo_decay_rises_monotonically_and_caps(tmp_path):
    """decay = clamp(echo * 0.6, 0.0, 0.7): monotone in echo, capped at 0.7."""
    decays = []
    for echo in (0.0, 0.1, 0.5, 0.9, 1.0, 1.5, 5.0):
        proj = _project(tmp_path)
        _attach_tone(proj, tmp_path, "music", volume=1.0, echo=echo)
        plan = mix.plan_audio(proj, total=5.0)
        decays.append(_echo_decay(plan.chains[0]))
    assert decays[0] is None  # echo off → no filter
    values = [d for d in decays if d is not None]
    assert values == sorted(values)  # monotonically rising
    assert all(v <= 0.7 for v in values)
    assert max(values) == pytest.approx(0.7)


def test_plan_echo_on_voice_leaves_music_plain(tmp_path):
    """Per-channel independence (FR-008): echo on voice never touches music."""
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5)
    _attach_tone(proj, tmp_path, "voice", volume=0.5, echo=0.8)
    plan = mix.plan_audio(proj, total=5.0)
    music_chain, voice_chain = plan.chains[0], plan.chains[1]
    assert "aecho" not in music_chain
    assert "aecho" in voice_chain


def test_signature_includes_echo(tmp_path):
    """Any echo change makes the signature differ, so the next play re-bakes."""
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5, echo=0.3)
    before = mix.mix_signature(proj)
    proj.movie.audio.echo = 0.7
    proj.save()
    assert mix.mix_signature(proj) != before


def test_mix_planning_is_pure_data_no_processes_started(tmp_path, monkeypatch):
    """US5/FRO015-FR016 purity guard: building the plan and the signature is pure
    data work — a runaway process would call subprocess (ffmpeg), and shaping
    must only ever run inside the bounded render action, never in the background
    (Constitution II)."""
    def _forbid(*_args, **_kwargs):
        raise AssertionError("planning must not start a process")

    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5, echo=0.4, fade_in=0.2, fade_out=0.3)
    monkeypatch.setattr(mix.subprocess, "run", _forbid)
    plan = mix.plan_audio(proj, total=5.0)  # pure: never launches ffmpeg
    assert plan.total == 5.0
    sig = mix.mix_signature(proj)  # pure: never touches the disk beyond reads
    assert ("music", "music.wav", 0.0, 0.5, 0.4, 0.2, 0.3) in sig


# -- mix_signature ------------------------------------------------------------


def test_signature_changes_with_volume(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5)
    before = mix.mix_signature(proj)
    proj.movie.audio.volume = 0.9
    proj.save()
    assert mix.mix_signature(proj) != before


def test_signature_drops_missing_channel(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5)
    _attach_tone(proj, tmp_path, "voice", volume=0.5)
    with_both = mix.mix_signature(proj)
    proj.movie.voice.filename = "gone.wav"
    proj.save()
    assert mix.mix_signature(proj) != with_both


def test_signature_constant_for_unchanged_project(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "music", volume=0.5)
    _attach_tone(proj, tmp_path, "voice", volume=0.5)
    assert mix.mix_signature(proj) == mix.mix_signature(proj)


# -- render_mix ---------------------------------------------------------------


def test_render_mix_silent_wav_when_no_audio(tmp_path):
    proj = _project(tmp_path)
    proj.movie.duration = 1.0  # single still: timeline = movie.duration (007)
    out = tmp_path / "preview.wav"
    progress = []
    mix.render_mix(proj, out, progress_cb=progress.append)
    assert out.is_file()
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == mix.WAV_RATE
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == int(1.0 * mix.WAV_RATE)
    assert progress == [1.0]


def test_render_mix_bakes_audio_to_wav(tmp_path):
    proj = _project(tmp_path)
    proj.movie.duration = 1.0  # single still: timeline = movie.duration (007)
    _attach_tone(proj, tmp_path, "music", volume=0.8, fade_in=0.1, fade_out=0.1)
    out = tmp_path / "preview.wav"
    mix.render_mix(proj, out)
    assert out.is_file()
    assert media.audio_duration(out) == pytest.approx(1.0, abs=0.1)
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == mix.WAV_RATE
        assert wf.getsampwidth() == 2


def test_render_mix_raises_on_bad_input(tmp_path):
    proj = _project(tmp_path)
    bad = proj.media_dir() / "bad.wav"
    bad.write_bytes(b"this is not audio at all")
    proj.movie.audio = model_mod.MediaItem(kind="audio", filename="bad.wav", volume=1.0)
    proj.save()
    out = tmp_path / "preview.wav"
    with pytest.raises(mix.MixError):
        mix.render_mix(proj, out)


def test_render_mix_wav_is_pcm16_stereo_44k(tmp_path):
    proj = _project(tmp_path)
    _attach_tone(proj, tmp_path, "voice")
    out = tmp_path / "preview.wav"
    mix.render_mix(proj, out)
    with open(out, "rb") as f:
        assert f.read(4) == b"RIFF"
        f.seek(8)
        assert f.read(4) == b"WAVE"
        fmt = f.read(8)
        assert fmt[:4] == b"fmt "
        size = struct.unpack("<I", fmt[4:8])[0]
        tag, channels, rate = struct.unpack("<HHI", f.read(8))
        assert tag == 1
        assert channels == 2
        assert rate == mix.WAV_RATE
        assert size >= 16


# -- T026: the mix never touches images or video (audio-only, no GPU) -----------


def test_plan_audio_never_references_images_or_canvas(tmp_path):
    """A two-channel plan carries nothing from the video side: no scale/xfade/
    canvas filters and no image paths — the audio bake cannot drag in a GPU."""
    proj = _project(tmp_path, images=2)
    proj.movie.crossfade = 0.5
    _attach_tone(proj, tmp_path, "music", volume=0.7)
    _attach_tone(proj, tmp_path, "voice", volume=0.3)
    plan = mix.plan_audio(proj, total=mix.timeline_duration(proj))
    joined = " ".join(plan.chains)
    for forbidden in ("scale=", "xfade", "format=yuv420p", "-c:v", "libx264", "-loop"):
        assert forbidden not in joined
    assert not any("img0" in str(a) or "img1" in str(a) for a in plan.inputs)
    assert all(a == "-i" or str(a).endswith(".wav") for a in plan.inputs)


def test_render_mix_command_is_audio_only(tmp_path, monkeypatch):
    """The exact ffmpeg command render_mix runs contains only audio inputs and
    filters — no video codec, no loop, no image ever referenced."""
    proj = _project(tmp_path, images=2)
    _attach_tone(proj, tmp_path, "music", volume=0.7)
    out = tmp_path / "preview.wav"
    captured = {}

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kw):
        captured["cmd"] = [str(c) for c in cmd]
        return Result()

    monkeypatch.setattr(mix.subprocess, "run", fake_run)
    mix.render_mix(proj, out)
    cmd = captured["cmd"]
    assert "-c:a" in cmd and "pcm_s16le" in cmd
    for forbidden in ("-c:v", "-loop", "libx264", "scale=", "xfade", "format=yuv420p"):
        assert forbidden not in cmd
    assert "-map" in cmd and "[aout]" in cmd
    assert not any("img" in str(a) for a in cmd)

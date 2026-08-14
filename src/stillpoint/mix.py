"""The shared mix path — the keystone (FR-006, Constitution III).

This module is the ONE place the two-channel audio mix is described. The
preview bakes :func:`plan_audio` to a WAV and plays it; ``render.build_spec``
composes the identical plan into the mp4 Spec 8's export drives. Because both
consumers call one function, preview and export can never diverge in channels,
balance, timing, or length (FR-006, FR-007).

Pure and display-free at module top (no Tk), mirroring ``render.py``: it builds
ffmpeg commands as data and runs them only inside :func:`render_mix`. A channel
whose file is missing is skipped into silence by the plan itself (FR-010) — in
both preview and export, identically, because both use this same plan.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .media import ffmpeg_path

#: The two fixed roles, in the fixed order the spec mandates (music, voice).
CHANNEL_ROLES = (("music", "audio"), ("voice", "voice"))

#: The preview's baked format: 16-bit PCM, 44.1 kHz, stereo WAV.
WAV_RATE = 44100
WAV_CHANNELS = 2
WAV_SAMPLE_BYTES = 2


class MixError(RuntimeError):
    """A mix-bake failure that ``classify_playback_error`` can bucket."""


@dataclass
class AudioPlan:
    """The two-channel mix as ffmpeg data.

    ``inputs`` is the ``-i`` args for the contributing channels; ``chains`` are
    filter fragments whose final output is ``[aout]``; ``has_audio`` is false
    when no channel contributes (the mix is pure silence); ``total`` is the mix
    length in seconds (always the project timeline).
    """

    inputs: list[str]
    chains: list[str]
    has_audio: bool
    total: float


def timeline_duration(project) -> float:
    """Seconds of finished film — the mix length, equal to the export's."""
    from .render import timeline_duration as _render_timeline

    return _render_timeline(project)


def _channel_chain(item, idx: int, total: float, label: str) -> str:
    """One channel's filter chain: trim to the timeline, shape, pad, cut."""
    chain = (
        f"[{idx}:a]"
        f"atrim=start={item.in_point:.3f}:end={item.in_point + total:.3f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={item.volume:.3f}"
    )
    if item.fade_in > 0:
        chain += f",afade=t=in:st=0:d={item.fade_in:.3f}"
    if item.fade_out > 0:
        chain += f",afade=t=out:st={total - item.fade_out:.3f}:d={item.fade_out:.3f}"
    chain += f",apad,atrim=0:{total:.3f}[{label}]"
    return chain


def plan_audio(project, total: float, *, index_offset: int = 0) -> AudioPlan:
    """Describe the mix for ``total`` seconds of timeline.

    A channel contributes iff its role is recorded (``movie.audio`` for music,
    ``movie.voice`` for voice) **and** its file is readable on disk; a
    recorded-but-missing file is skipped into silence (FR-010). Two channels
    sum with ``amix=inputs=2:normalize=0`` — ``normalize=0`` is mandatory so the
    stored balance is never rescaled; one channel is that chain alone; none is
    pure silence (``has_audio=False``). ``index_offset`` lets ``render.build_spec``
    place the audio inputs after the image inputs.
    """
    inputs: list[str] = []
    chains: list[str] = []
    contributing: list = []
    for _role, attr in CHANNEL_ROLES:
        item = getattr(project.movie, attr)
        if item is None:
            continue
        if not project.media_file(item).is_file():
            continue  # missing file → this channel is silence (FR-010)
        contributing.append(item)
    for k, item in enumerate(contributing):
        inputs += ["-i", str(project.media_file(item))]
        chains.append(_channel_chain(item, index_offset + k, total, f"c{k}"))
    if len(contributing) == 2:
        chains.append("[c0][c1]amix=inputs=2:normalize=0[aout]")
    elif len(contributing) == 1:
        chains[0] = chains[0].replace("[c0]", "[aout]")
    return AudioPlan(inputs=inputs, chains=chains, has_audio=bool(contributing), total=total)


def mix_signature(project) -> tuple:
    """A stable, hashable signature of everything the mix depends on.

    For each recorded-and-readable channel: ``(role, filename, in_point,
    volume, fade_in, fade_out)``, plus ``total``. Preview uses it to skip a
    re-bake when nothing changed; any change in stored settings or channels
    makes it differ, so the next play-from-stop re-bakes with the new balance
    (FR-005, FR-015).
    """
    channels: list[tuple] = []
    for role, attr in CHANNEL_ROLES:
        item = getattr(project.movie, attr)
        if item is None:
            continue
        if not project.media_file(item).is_file():
            continue
        channels.append((role, item.filename, item.in_point, item.volume, item.fade_in, item.fade_out))
    return tuple(channels) + (timeline_duration(project),)


def _write_silent_wav(out_path: Path, total: float) -> None:
    """Write a silent 16-bit PCM 44.1 kHz stereo WAV of length ``total`` (no ffmpeg)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(0, int(total * WAV_RATE))
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(WAV_CHANNELS)
        wf.setsampwidth(WAV_SAMPLE_BYTES)
        wf.setframerate(WAV_RATE)
        wf.writeframes(b"\x00\x00" * (frames * WAV_CHANNELS))


def render_mix(project, out_path, *, progress_cb=None) -> Path:
    """Bake the preview's file: run ffmpeg on :func:`plan_audio` to a WAV.

    Produces 16-bit PCM, 44.1 kHz, stereo (PCM16). When no channel contributes
    it writes a silent WAV of length ``total`` instead of running ffmpeg. On
    failure raises :class:`MixError`, which ``classify_playback_error`` can map
    to a plain bucket. Never touches the project folder or ``project.json``
    (FR-012).
    """
    out_path = Path(out_path)
    plan = plan_audio(project, timeline_duration(project))
    if not plan.has_audio:
        _write_silent_wav(out_path, plan.total)
        if progress_cb:
            progress_cb(1.0)
        return out_path
    cmd = [
        str(ffmpeg_path()),
        "-y",
        "-v", "error",
        *plan.inputs,
        "-filter_complex", ";".join(plan.chains),
        "-map", "[aout]",
        "-ac", str(WAV_CHANNELS),
        "-ar", str(WAV_RATE),
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as exc:
        raise MixError(f"could not start ffmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-800:]
        raise MixError(f"mix render failed:\n{detail}")
    if progress_cb:
        progress_cb(1.0)
    return out_path

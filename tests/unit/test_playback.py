"""T010/T006–T008: playback session + winmm sink + error classification.

Headless: a FakeSink records device calls, a FakeBaker records bake calls, and
no real audio device is touched. The only sink code exercised directly is the
pure RIFF parser (_parse_wav) which needs no device.
"""

import wave

import pytest

from stillpoint import playback as pb
from stillpoint.model import Project


class FakeSink:
    """Records the transport calls the session makes; never touches a device."""

    def __init__(self):
        self.calls = []
        self._done = False
        self._pos_seconds = 0.0

    def open(self, path, *, start_seconds=0.0):
        self.calls.append(("open", str(path), start_seconds))

    def play(self):
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def restart(self):
        self.calls.append(("restart",))

    def done(self):
        return self._done

    def position_seconds(self):
        return self._pos_seconds

    def stop(self):
        self.calls.append(("stop",))


class FakeBaker:
    def __init__(self):
        self.calls = []

    def __call__(self, project, out_path, **kw):
        self.calls.append((str(out_path),))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"\x00" * 800)


def _session(**kw):
    sink = kw.pop("sink", None) or FakeSink()
    baker = kw.pop("baker", None) or FakeBaker()
    return pb.PlaybackSession(sink=sink, baker=baker, signature_fn=lambda p: "sig"), sink, baker


def _project():
    return Project(title="T", directory=None)


# -- state machine -------------------------------------------------------------


def test_prepare_play_from_stopped_bakes(tmp_path):
    session, sink, baker = _session()
    assert session.prepare_play(_project()) == "bake"
    assert session.state == pb.BAKING
    assert session.wav_path is not None
    assert baker.calls == []
    assert sink.calls == []


def test_bake_done_plays_the_baked_file(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = session.wav_path
    session.bake_done()
    assert session.state == pb.PLAYING
    assert sink.calls == [("open", str(wav), 0.0), ("play",)]


def test_play_without_bake_uses_wav_path(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = tmp_path / "wav.wav"
    session.bake_done(wav)
    assert sink.calls[-2] == ("open", str(wav), 0.0)


def test_unchanged_signature_plays_directly(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    session.check_finished()
    action = session.prepare_play(_project())  # same signature -> no re-bake
    assert action == "play"
    assert session.state == pb.PLAYING
    assert baker.calls == []  # never re-baked


def test_changed_signature_bakes_again(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    session.check_finished()
    session.stop()
    session._signature_fn = lambda p: "different"
    action = session.prepare_play(_project())
    assert action == "bake"
    assert session.state == pb.BAKING


def test_bake_failed_returns_to_stopped(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_failed()
    assert session.state == pb.STOPPED
    assert sink.calls == []


def test_pause_and_resume(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    session.pause()
    assert session.state == pb.PAUSED
    assert sink.calls[-1] == ("pause",)
    assert session.prepare_play(_project()) == "resume"
    assert session.state == pb.PLAYING
    assert sink.calls[-1] == ("resume",)


def test_resume_after_settings_change_rebakes_not_resumes(tmp_path):
    """FR-015: a change is heard the next time playback starts, even from pause."""
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    session.pause()
    session._signature_fn = lambda p: "new-balance"
    assert session.prepare_play(_project()) == "bake"
    assert session.state == pb.BAKING


def test_settings_change_while_playing_rebakes_and_keeps_spot(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = session.wav_path
    session.bake_done()
    sink._pos_seconds = 12.5
    session._signature_fn = lambda p: "new-balance"
    assert session.settings_changed(_project()) == "bake"
    assert session.state == pb.BAKING
    assert sink.calls[-1] == ("stop",)
    session.bake_done(wav)
    assert session.state == pb.PLAYING
    assert sink.calls[-2] == ("open", str(wav), 12.5)
    assert sink.calls[-1] == ("play",)


def test_settings_change_while_paused_rebakes_and_keeps_spot(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = session.wav_path
    session.bake_done()
    session.pause()
    sink._pos_seconds = 7.0
    session._signature_fn = lambda p: "new-balance"
    assert session.settings_changed(_project()) == "bake"
    session.bake_done(wav)
    assert session.state == pb.PLAYING
    assert sink.calls[-2] == ("open", str(wav), 7.0)


def test_settings_change_with_unchanged_signature_is_noop(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    assert session.settings_changed(_project()) is None
    assert session.state == pb.PLAYING
    assert ("stop",) not in sink.calls


def test_settings_change_when_stopped_is_noop(tmp_path):
    session, sink, baker = _session()
    assert session.settings_changed(_project()) is None
    assert session.state == pb.STOPPED


def test_settings_change_after_finish_resumes_from_top(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = session.wav_path
    session.bake_done()
    sink._done = True
    assert session.check_finished() is True
    sink._pos_seconds = 30.0
    session._signature_fn = lambda p: "new-balance"
    assert session.settings_changed(_project()) == "bake"
    session.bake_done(wav)
    assert sink.calls[-2] == ("open", str(wav), 0.0)


def test_needs_rebake_flags_mid_bake_changes(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    assert session.needs_rebake(_project()) is False
    session._signature_fn = lambda p: "new-balance"
    assert session.needs_rebake(_project()) is True


def test_start_over_restarts_from_top(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    session.pause()
    session.start_over()
    assert session.state == pb.PLAYING
    assert sink.calls[-1] == ("restart",)


def test_check_finished_transitions(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    session.bake_done()
    sink._done = True
    assert session.check_finished() is True
    assert session.state == pb.FINISHED


def test_stop_releases_sink_and_cleans_temp(tmp_path):
    session, sink, baker = _session()
    session.prepare_play(_project())
    wav = session.wav_path
    baker(None, wav)
    session.bake_done()
    assert wav.is_file()
    session.stop()
    assert session.state == pb.STOPPED
    assert sink.calls[-1] == ("stop",)
    assert not wav.exists()


def test_sync_play_bakes_and_plays(tmp_path):
    session, sink, baker = _session()
    proj = _project()
    session.play(proj)
    assert session.state == pb.PLAYING
    assert len(baker.calls) == 1
    assert sink.calls == [("open", baker.calls[0][0], 0.0), ("play",)]


def test_finished_then_play_rebakes(tmp_path):
    session, sink, baker = _session()
    proj = _project()
    session.play(proj)
    sink._done = True
    assert session.check_finished() is True
    assert session.state == pb.FINISHED
    assert session.prepare_play(proj) in ("play", "bake")  # never 'resume'


# -- error classification (FR-011) ---------------------------------------------


def test_classify_playback_error_bucket_unreadable():
    kind, message = pb.classify_playback_error(FileNotFoundError("no such file"))
    assert kind == "unreadable"
    assert message == pb.UNREADABLE_MESSAGE


def test_classify_playback_error_bucket_other():
    kind, message = pb.classify_playback_error(RuntimeError("waveOutOpen failed"))
    assert kind == "other"
    assert message == pb.OTHER_MESSAGE


def test_classify_playback_error_buckets_unreadable_tokens():
    kind, _ = pb.classify_playback_error(RuntimeError("invalid data found when processing input"))
    assert kind == "unreadable"


def test_classify_playback_error_passthrough_playback_error():
    exc = pb.PlaybackError("unreadable", "custom message")
    kind, message = pb.classify_playback_error(exc)
    assert kind == "unreadable"
    assert message == "custom message"


# -- RIFF parsing (pure; no device needed) ------------------------------------


def _write_wav(path, *, channels=1, rate=8000, frames=400):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00" * frames * channels * 2)


def test_parse_wav_reads_header(tmp_path):
    path = tmp_path / "t.wav"
    _write_wav(path, channels=1, rate=8000, frames=400)
    ch, rate, bits, offset, size = pb._parse_wav(path)
    assert (ch, rate, bits) == (1, 8000, 16)
    assert size == 400 * 2


def test_parse_wav_rejects_non_wave(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("not a wave file at all, sorry")
    with pytest.raises(pb.WaveOutError):
        pb._parse_wav(path)


def test_parse_wav_accepts_2_channel_stereo(tmp_path):
    path = tmp_path / "s.wav"
    _write_wav(path, channels=2, rate=44100, frames=100)
    ch, rate, bits, offset, size = pb._parse_wav(path)
    assert (ch, rate, bits) == (2, 44100, 16)
    assert size == 100 * 4

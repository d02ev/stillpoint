"""Headless unit tests for the local-audio import service (spec 004).

Every rule (error buckets, staging/atomicity, unique names, original never
touched, no time cap with progress, set_voice save/load, persistence across a
folder move) is tested without any real ffmpeg and without any network: the
conversion is a fake that writes bytes into the destination.
"""

import os
import subprocess

import pytest

from stillpoint import import_audio, media, model as model_mod


def _project(tmp_path, title="Import Proj"):
    return model_mod.new_project(title, tmp_path / title, "t0")


def _fake_convert(converter=None):
    """A fake ``media.convert_to_m4a`` that writes the output file.

    ``converter(src, out, *, progress_cb, timeout)``, if given, runs first so a
    test can drive progress callbacks or raise a failure.
    """

    def convert(src, out, *, progress_cb=None, timeout=None):
        if converter:
            converter(src, out, progress_cb=progress_cb, timeout=timeout)
        out.write_bytes(b"aac-data")

    return convert


def _write_source(tmp_path, name="my voice.wav", data=b"original") :
    source = tmp_path / name
    source.write_bytes(data)
    return source


# -- classify_import_error (FR-010) -------------------------------------------


def test_classify_import_error_decode_failure_is_unreadable():
    exc = RuntimeError("audio conversion failed:\nInvalid data found when processing input")
    assert import_audio.classify_import_error(exc) == ("unreadable", import_audio.UNREADABLE_MESSAGE)


@pytest.mark.parametrize(
    "message",
    [
        "Invalid data found",
        "moov atom not found",
        "Could not find codec parameters",
        "no audio streams",
        "Stream 0 is not audio",
    ],
)
def test_classify_import_error_unreadable_tokens(message):
    assert import_audio.classify_import_error(RuntimeError(message))[0] == "unreadable"


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError("Access is denied"),
        FileNotFoundError(2, "No such file"),
        OSError("The device is not connected"),
    ],
)
def test_classify_import_error_io_failures_are_unreadable(exc):
    assert import_audio.classify_import_error(exc)[0] == "unreadable"


def test_classify_import_error_unknown_is_other():
    assert import_audio.classify_import_error(ValueError("boom")) == ("other", import_audio.OTHER_MESSAGE)


def test_classify_import_error_passes_import_error_through():
    error = import_audio.ImportError("other", "custom")
    assert import_audio.classify_import_error(error) == ("other", "custom")


# -- import_local_audio success (FR-004/005/006/008/012) ------------------------


def test_import_local_audio_stores_converted_copy(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path)
    events = []
    filename = import_audio.import_local_audio(project, source, progress=events.append, convert=_fake_convert())

    assert filename == "my voice.m4a"
    stored = project.media_dir() / filename
    assert stored.is_file()
    assert stored.read_bytes() == b"aac-data"
    assert source.read_bytes() == b"original"  # the original is only ever read (FR-005)
    assert events[0].state == "importing"
    assert events[-1].state == "done"


def test_import_local_audio_done_event_carries_filename(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="a.wav")
    events = []
    import_audio.import_local_audio(project, source, progress=events.append, convert=_fake_convert())
    assert events[-1].state == "done"
    assert events[-1].detail == "a.m4a"


def test_import_local_audio_uses_unique_names(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="Same.wav")
    first = import_audio.import_local_audio(project, source, convert=_fake_convert())
    second = import_audio.import_local_audio(project, source, convert=_fake_convert())
    assert first == "Same.m4a"
    assert second == "Same (2).m4a"


def test_import_local_audio_cleans_up_temp_dir(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="a.wav")
    import_audio.import_local_audio(project, source, convert=_fake_convert())
    leftovers = [p for p in project.media_dir().iterdir() if p.name.startswith(".stillpoint-import-")]
    assert leftovers == []
    assert sorted(p.name for p in project.media_dir().iterdir()) == ["a.m4a"]


def test_import_local_audio_sanitizes_forbidden_names(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="my:voice?*.wav")
    filename = import_audio.import_local_audio(project, source, convert=_fake_convert())
    assert ":" not in filename and "?" not in filename
    assert (project.media_dir() / filename).is_file()


# -- import_local_audio failure (FR-008/010) -------------------------------------


def test_import_local_audio_failure_leaves_no_partial_file(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="bad.mp3", data=b"not audio")

    def failing_convert(src, out, *, progress_cb=None, timeout=None):
        raise RuntimeError("Invalid data found when processing input")

    with pytest.raises(import_audio.ImportError) as excinfo:
        import_audio.import_local_audio(project, source, convert=failing_convert)
    assert excinfo.value.kind == "unreadable"
    assert import_audio.UNREADABLE_MESSAGE in str(excinfo.value)
    assert list(project.media_dir().iterdir()) == []


def test_import_local_audio_failure_leaves_project_and_source_untouched(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="bad.mp3", data=b"original-bytes")
    before_file = project.project_file.read_bytes()
    before_media = sorted(p.name for p in project.media_dir().iterdir())

    def failing_convert(src, out, *, progress_cb=None, timeout=None):
        raise PermissionError("locked")

    with pytest.raises(import_audio.ImportError) as excinfo:
        import_audio.import_local_audio(project, source, convert=failing_convert)
    assert excinfo.value.kind == "unreadable"
    assert project.project_file.read_bytes() == before_file
    assert sorted(p.name for p in project.media_dir().iterdir()) == before_media
    assert source.read_bytes() == b"original-bytes"


def test_import_local_audio_rejects_missing_source(tmp_path):
    project = _project(tmp_path)
    events = []
    missing = tmp_path / "nope.mp3"
    with pytest.raises(import_audio.ImportError) as excinfo:
        import_audio.import_local_audio(project, missing, progress=events.append)
    assert excinfo.value.kind == "unreadable"
    assert events and events[-1].state == "error"
    assert events[-1].detail == import_audio.UNREADABLE_MESSAGE


# -- import_local_audio progress + no cap (US4, FR-009) --------------------------


def test_import_local_audio_passes_no_time_cap_and_progress(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="big.wav")
    calls = []

    def convert(src, out, *, progress_cb=None, timeout=None):
        calls.append((src, out, progress_cb, timeout))
        progress_cb(0.25)
        progress_cb(0.75)
        out.write_bytes(b"x")

    events = []
    import_audio.import_local_audio(project, source, progress=events.append, convert=convert)
    assert calls and calls[0][3] is None  # timeout=None → never cut off (FR-009)
    assert calls[0][2] is not None  # the progress callback is wired through
    importing = [e for e in events if e.state == "importing"]
    assert [e.value for e in importing] == [0.0, 0.25, 0.75]
    assert importing[0].detail == import_audio.IMPORTING
    assert importing[1].detail == "Importing your audio … 25%"
    assert importing[2].detail == "Importing your audio … 75%"
    assert events[-1].state == "done"


def test_import_local_audio_indeterminate_when_no_fraction(tmp_path):
    project = _project(tmp_path)
    source = _write_source(tmp_path, name="a.wav")

    def convert(src, out, *, progress_cb=None, timeout=None):
        out.write_bytes(b"x")

    events = []
    import_audio.import_local_audio(project, source, progress=events.append, convert=convert)
    importing = [e for e in events if e.state == "importing"]
    assert importing
    assert all(e.value == 0.0 for e in importing)
    assert all(e.detail == import_audio.IMPORTING for e in importing)
    assert events[-1].state == "done"


# -- convert_to_m4a parameter plumbing (research Decision 2) -----------------------


class _FakeStream:
    def __init__(self, lines):
        self._lines = [line.encode() for line in lines]

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return b""


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = _FakeStream(lines)
        self.stderr = _FakeStream([])
        self.returncode = 0

    def wait(self):
        return 0


def _fake_mkstemp(tmp_path):
    def mkstemp(prefix, suffix, dir):
        fd = os.open(os.devnull, os.O_RDONLY)
        return fd, str(tmp_path / "a.m4a.xyz.tmp")

    return mkstemp


def test_convert_to_m4a_preserves_600s_default(tmp_path, monkeypatch):
    src = _write_source(tmp_path, name="a.wav")
    out = tmp_path / "a.m4a"
    monkeypatch.setattr("stillpoint.media.tempfile.mkstemp", _fake_mkstemp(tmp_path))
    captured = {}

    def fake_run(cmd, capture_output=None, check=None, timeout=None):
        captured["timeout"] = timeout
        (tmp_path / "a.m4a.xyz.tmp").write_bytes(b"m4a-data")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("stillpoint.media.subprocess.run", fake_run)
    media.convert_to_m4a(src, out)
    assert captured["timeout"] == 600
    assert out.read_bytes() == b"m4a-data"


def test_convert_to_m4a_timeout_none_disables_cap(tmp_path, monkeypatch):
    src = _write_source(tmp_path, name="a.wav")
    out = tmp_path / "a.m4a"
    monkeypatch.setattr("stillpoint.media.tempfile.mkstemp", _fake_mkstemp(tmp_path))
    captured = {}

    def fake_run(cmd, capture_output=None, check=None, timeout=None):
        captured["timeout"] = timeout
        (tmp_path / "a.m4a.xyz.tmp").write_bytes(b"m4a-data")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("stillpoint.media.subprocess.run", fake_run)
    media.convert_to_m4a(src, out, timeout=None)
    assert captured["timeout"] is None


def test_convert_to_m4a_progress_streams_fraction_then_done(tmp_path, monkeypatch):
    src = _write_source(tmp_path, name="a.wav")
    out = tmp_path / "a.m4a"
    monkeypatch.setattr("stillpoint.media.tempfile.mkstemp", _fake_mkstemp(tmp_path))
    monkeypatch.setattr("stillpoint.media.probe_duration", lambda path: 10.0)

    def fake_popen(cmd, stdout=None, stderr=None, stdin=None):
        (tmp_path / "a.m4a.xyz.tmp").write_bytes(b"m4a-data")
        return _FakeProcess(["out_time_us=5000000", "progress=continue"])

    monkeypatch.setattr("stillpoint.media.subprocess.Popen", fake_popen)
    calls = []
    media.convert_to_m4a(src, out, progress_cb=calls.append)
    assert calls == [pytest.approx(0.5), 1.0]
    assert out.read_bytes() == b"m4a-data"


# -- Project.set_voice (FR-006, no schema change) --------------------------------


def test_set_voice_saves_and_roundtrips(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "voice.m4a").write_bytes(b"x")
    item = project.set_voice("voice.m4a")
    assert item.kind == "audio"
    assert item.filename == "voice.m4a"
    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.voice is not None
    assert loaded.movie.voice.filename == "voice.m4a"


def test_set_voice_swaps_previous(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "one.m4a").write_bytes(b"1")
    (project.media_dir() / "two.m4a").write_bytes(b"2")
    project.set_voice("one.m4a")
    project.set_voice("two.m4a")
    assert project.movie.voice.filename == "two.m4a"


def test_set_voice_rejects_missing_file(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        project.set_voice("gone.m4a")
    assert project.movie.voice is None


# -- persistence across save/reopen and folder move (US3, FR-007) -----------------


def test_imported_roles_restore_on_reopen(tmp_path):
    project = _project(tmp_path)
    voice = _write_source(tmp_path, name="voice.wav", data=b"v")
    music = _write_source(tmp_path, name="music.wav", data=b"m")
    project.set_voice(import_audio.import_local_audio(project, voice, convert=_fake_convert()))
    project.set_background_music(import_audio.import_local_audio(project, music, convert=_fake_convert()))

    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.voice.filename == "voice.m4a"
    assert loaded.movie.audio.filename == "music.m4a"


def test_imported_audio_survives_project_folder_move(tmp_path):
    project = _project(tmp_path)
    voice = _write_source(tmp_path, name="voice.wav", data=b"v")
    music = _write_source(tmp_path, name="music.wav", data=b"m")
    project.set_voice(import_audio.import_local_audio(project, voice, convert=_fake_convert()))
    project.set_background_music(import_audio.import_local_audio(project, music, convert=_fake_convert()))

    moved = tmp_path / "Moved Folder"
    project.directory.rename(moved)
    loaded = model_mod.Project.load(moved)
    assert loaded.movie.voice.filename == "voice.m4a"
    assert loaded.movie.audio.filename == "music.m4a"
    assert (moved / "media" / "voice.m4a").is_file()
    assert (moved / "media" / "music.m4a").is_file()

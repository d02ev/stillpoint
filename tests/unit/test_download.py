"""Headless unit tests for the YouTube audio download service.

Every rule (URL shape, error classification, standard format, list derivation,
job state machine with a fake fetcher, one-at-a-time guard, import + save) is
tested without any network and without importing yt-dlp.
"""

import os
import time
import urllib.error

import pytest

from stillpoint import download, model as model_mod, youtube


def _project(tmp_path, title="Music Proj"):
    return model_mod.new_project(title, tmp_path / title, "t0")


def _fetch_from_file(source_name: str, title: str = "Ocean Waves"):
    """A fake fetcher that writes ``source_name`` into the temp dir."""

    def fetch(url, temp_dir, *, on_progress=None, should_stop=None):
        source = temp_dir / source_name
        source.write_bytes(b"fake-audio")
        return title, source

    return fetch


# -- URL shape (FR-002) -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "http://www.youtube.com/watch?v=abc",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/embed/abc123",
        "https://www.youtube.com/live/abc123",
        "https://www.youtube.com/watch?v=abc&list=PLxyz",
    ],
)
def test_is_video_url_accepts_single_videos(url):
    assert youtube.is_video_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "hello world",
        "https://example.com/watch?v=x",
        "ftp://youtube.com/watch?v=x",
        "https://www.youtube.com/playlist?list=PLxyz",
        "https://www.youtube.com/@SomeChannel",
        "https://www.youtube.com/channel/UCxyz",
        "https://www.youtube.com/user/Someone",
        "https://www.youtube.com/",
        "https://youtu.be/",
        "https://youtu.be/abc/extra",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?list=PLxyz",
    ],
)
def test_is_video_url_rejects_non_videos(url):
    assert not youtube.is_video_url(url)


# -- error classification (FR-011/012/013) -------------------------------------


def test_classify_unsupported_error_is_bad_link():
    exc = type("UnsupportedError", (RuntimeError,), {})("Unsupported URL: https://x")
    assert youtube.classify_error(exc) == ("bad_link", youtube.BAD_LINK_MESSAGE)


@pytest.mark.parametrize(
    "message",
    [
        "Video unavailable",
        "This video is private",
        "This video is no longer available",
        "HTTP Error 404: Not Found",
    ],
)
def test_classify_bad_link_messages(message):
    assert youtube.classify_error(RuntimeError(message)) == ("bad_link", youtube.BAD_LINK_MESSAGE)


def test_classify_url_error_is_no_connection():
    exc = urllib.error.URLError("getaddrinfo failed")
    assert youtube.classify_error(exc) == ("no_connection", youtube.NO_CONNECTION_MESSAGE)


@pytest.mark.parametrize("exc", [ConnectionError("refused"), TimeoutError("timed out")])
def test_classify_network_exceptions_are_no_connection(exc):
    assert youtube.classify_error(exc)[0] == "no_connection"


def test_classify_unknown_is_other():
    assert youtube.classify_error(ValueError("boom")) == ("other", youtube.OTHER_MESSAGE)


def test_classify_passes_download_error_through():
    error = youtube.DownloadError("no_connection", "custom")
    assert youtube.classify_error(error) == ("no_connection", "custom")


def test_classify_reraised_stop():
    with pytest.raises(youtube.DownloadStopped):
        youtube.classify_error(youtube.DownloadStopped())


# -- download options (audio only, progress hook) -------------------------------


def test_download_options_are_audio_only_with_hook():
    events = []
    options = youtube.build_download_options(r"X:\%(title)s.%(ext)s", events.append, lambda: False)
    assert options["format"] == youtube.EXTRACT_FORMAT
    assert options["noplaylist"] is True
    assert options["outtmpl"] == r"X:\%(title)s.%(ext)s"
    hook = options["progress_hooks"][0]
    hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 45})
    assert events == [("downloading", 0.45, "Downloading … 45%")]


def test_download_options_hook_stops():
    options = youtube.build_download_options("x", None, lambda: True)
    with pytest.raises(youtube.DownloadStopped):
        options["progress_hooks"][0]({"status": "downloading"})


def test_download_options_ignore_other_statuses():
    events = []
    options = youtube.build_download_options("x", events.append, lambda: False)
    options["progress_hooks"][0]({"status": "finished"})
    assert events == []


# -- downloaded-track list (FR-009/FR-010) -------------------------------------


def _write_audio(media_dir, name, mtime):
    path = media_dir / name
    path.write_bytes(b"data")
    os.utime(path, (mtime, mtime))


def test_list_downloaded_tracks_newest_first(tmp_path):
    project = _project(tmp_path)
    media = project.media_dir()
    _write_audio(media, "a.m4a", time.time() - 30)
    _write_audio(media, "b.mp3", time.time() - 10)
    _write_audio(media, "c.opus", time.time() - 20)
    (media / "d.jpg").write_bytes(b"x")  # not audio
    (media / "e.txt").write_bytes(b"x")  # not audio
    (media / "sub").mkdir()  # not a file

    assert download.list_downloaded_tracks(project) == ["b.mp3", "c.opus", "a.m4a"]


def test_list_downloaded_tracks_empty(tmp_path):
    project = _project(tmp_path)
    assert download.list_downloaded_tracks(project) == []


def test_list_downloaded_tracks_appears_after_store(tmp_path):
    project = _project(tmp_path)
    _write_audio(project.media_dir(), "new.m4a", time.time())
    assert "new.m4a" in download.list_downloaded_tracks(project)


# -- download_track job (FR-003…FR-008, FR-022) --------------------------------


def test_download_track_stores_native_m4a(tmp_path):
    project = _project(tmp_path)
    events = []
    filename = download.download_track(
        project,
        "https://www.youtube.com/watch?v=abc",
        progress=events.append,
        fetch=_fetch_from_file("native.m4a"),
    )
    assert filename == "Ocean Waves.m4a"
    assert (project.media_dir() / filename).is_file()
    assert [e.state for e in events] == ["finding", "converting", "done"]


def test_download_track_transcodes_non_m4a(tmp_path, monkeypatch):
    project = _project(tmp_path)
    converted = []

    def fake_convert(src, out):
        out.write_bytes(b"aac-data")
        converted.append((src.name, out.name))

    monkeypatch.setattr("stillpoint.media.convert_to_m4a", fake_convert)
    filename = download.download_track(
        project,
        "https://youtu.be/abc",
        fetch=_fetch_from_file("native.webm"),
    )
    assert filename == "Ocean Waves.m4a"
    assert (project.media_dir() / filename).read_bytes() == b"aac-data"
    assert converted and converted[0][0] == "native.webm"


def test_download_track_uses_unique_names(tmp_path):
    project = _project(tmp_path)
    fetch = _fetch_from_file("x.m4a", title="Same")
    first = download.download_track(project, "https://www.youtube.com/watch?v=a", fetch=fetch)
    second = download.download_track(project, "https://www.youtube.com/watch?v=a", fetch=fetch)
    assert first == "Same.m4a"
    assert second == "Same (2).m4a"


def test_download_track_rejects_bad_link_without_fetch(tmp_path):
    project = _project(tmp_path)
    events = []
    with pytest.raises(youtube.DownloadError) as excinfo:
        download.download_track(project, "hello world", progress=events.append)
    assert excinfo.value.kind == "bad_link"
    assert events[-1].state == "error"
    assert events[-1].detail == youtube.BAD_LINK_MESSAGE
    assert download.list_downloaded_tracks(project) == []
    assert not project.project_file.read_text().count("audio")


def test_download_track_surfaces_classified_failure(tmp_path):
    project = _project(tmp_path)
    events = []

    def failing_fetch(url, temp_dir, *, on_progress=None, should_stop=None):
        raise urllib.error.URLError("no such host")

    with pytest.raises(youtube.DownloadError) as excinfo:
        download.download_track(
            project, "https://www.youtube.com/watch?v=abc",
            progress=events.append, fetch=failing_fetch,
        )
    assert excinfo.value.kind == "no_connection"
    assert events[-1].detail == youtube.NO_CONNECTION_MESSAGE
    assert download.list_downloaded_tracks(project) == []


def test_download_track_passes_classified_error_through(tmp_path):
    project = _project(tmp_path)

    def failing_fetch(url, temp_dir, *, on_progress=None, should_stop=None):
        raise youtube.DownloadError("bad_link", youtube.BAD_LINK_MESSAGE)

    with pytest.raises(youtube.DownloadError) as excinfo:
        download.download_track(project, "https://www.youtube.com/watch?v=abc", fetch=failing_fetch)
    assert excinfo.value.kind == "bad_link"


def test_download_track_stop_during_fetch_leaves_no_file(tmp_path):
    project = _project(tmp_path)
    events = []

    def stopping_fetch(url, temp_dir, *, on_progress=None, should_stop=None):
        raise youtube.DownloadStopped()

    with pytest.raises(youtube.DownloadStopped):
        download.download_track(
            project, "https://www.youtube.com/watch?v=abc",
            progress=events.append, fetch=stopping_fetch,
        )
    assert events[-1].state == "stopped"
    assert download.list_downloaded_tracks(project) == []


def test_download_track_stop_before_store_leaves_no_file(tmp_path):
    project = _project(tmp_path)
    events = []
    with pytest.raises(youtube.DownloadStopped):
        download.download_track(
            project, "https://www.youtube.com/watch?v=abc",
            progress=events.append,
            should_stop=lambda: True,
            fetch=_fetch_from_file("native.m4a"),
        )
    assert events[-1].state == "stopped"
    assert download.list_downloaded_tracks(project) == []


def test_download_track_one_at_a_time_guard(tmp_path):
    project = _project(tmp_path)
    assert download._manager.try_begin()
    try:
        with pytest.raises(youtube.DownloadError) as excinfo:
            download.download_track(project, "https://www.youtube.com/watch?v=abc")
        assert excinfo.value.kind == "other"
        assert download.BUSY in str(excinfo.value)
    finally:
        download._manager.end()


# -- set_background_music (FR-014…FR-017, R2) ----------------------------------


def test_set_background_music_saves_and_roundtrips(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "song.m4a").write_bytes(b"x")
    item = project.set_background_music("song.m4a")
    assert item.kind == "audio"
    assert item.filename == "song.m4a"
    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.audio is not None
    assert loaded.movie.audio.filename == "song.m4a"


def test_set_background_music_swaps_previous(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "one.m4a").write_bytes(b"1")
    (project.media_dir() / "two.m4a").write_bytes(b"2")
    project.set_background_music("one.m4a")
    project.set_background_music("two.m4a")
    assert project.movie.audio.filename == "two.m4a"


def test_set_background_music_rejects_missing_file(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        project.set_background_music("gone.m4a")
    assert project.movie.audio is None


def test_older_project_files_still_load(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "song.m4a").write_bytes(b"x")
    project.set_background_music("song.m4a")
    loaded = model_mod.Project.load(project.directory)
    assert loaded.movie.audio.filename == "song.m4a"

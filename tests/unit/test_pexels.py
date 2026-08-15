"""Headless unit tests for the Pexels image service.

Every rule (URL building, JSON parsing, key resolution, error classification,
the 1920x1080 JPEG standard, library derivation, set_background_image) is
tested with injectable fetchers and fabricated exceptions — no network, no GUI.
"""

import io
import json
import os
import socket
import ssl
import time
import urllib.error

import pytest
from PIL import Image

from stillpoint import model as model_mod, pexels


def _project(tmp_path, title="Pic Proj"):
    return model_mod.new_project(title, tmp_path / title, "t0")


def _photo(photo_id=123, alt="A calm lake at sunset"):
    return pexels.Photo(
        id=photo_id,
        alt=alt,
        photographer="Jane Doe",
        width=4000,
        height=2250,
        base_url=f"https://images.pexels.com/photos/{photo_id}/pic.jpeg",
    )


def _photo_payload(photo_id=123, alt="A calm lake at sunset", base=None):
    src = (base or f"https://images.pexels.com/photos/{photo_id}/pic.jpeg") + "?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
    return {
        "id": photo_id,
        "alt": alt,
        "photographer": "Jane Doe",
        "width": 4000,
        "height": 2250,
        "src": {"large2x": src},
    }


def _search_body(photos):
    return json.dumps({"photos": photos})


def _fake_fetch(body):
    def fetch(url, headers):
        fetch.calls.append((url, headers))
        return body

    fetch.calls = []
    return fetch


def _jpeg_bytes(width=1920, height=1080, color=(200, 30, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _write_image(media_dir, name, mtime):
    path = media_dir / name
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


# -- URL builders (data-model.md Entity 1) -------------------------------------


def test_url_builders_are_exact():
    photo = _photo()
    expected_thumb = "https://images.pexels.com/photos/123/pic.jpeg?auto=compress&cs=tinysrgb&fit=crop&w=480&h=270&dpr=1"
    expected_big = "https://images.pexels.com/photos/123/pic.jpeg?auto=compress&cs=tinysrgb&fit=crop&w=1920&h=1080&dpr=1"
    assert pexels.thumbnail_url(photo) == expected_thumb
    assert pexels.preview_url(photo) == expected_big
    assert pexels.download_url(photo) == expected_big  # preview == download (Constitution III)


# -- key resolution (Constitution V) -------------------------------------------


def test_resolve_api_key_uses_env_first(monkeypatch):
    monkeypatch.setenv("STILLPOINT_PEXELS_KEY", "env-key")
    assert pexels.resolve_api_key() == "env-key"


def test_resolve_api_key_falls_back_to_module(monkeypatch):
    monkeypatch.delenv("STILLPOINT_PEXELS_KEY", raising=False)
    from stillpoint import pexels_key as key_mod

    monkeypatch.setattr(key_mod, "PEXELS_API_KEY", "baked-key")
    assert pexels.resolve_api_key() == "baked-key"


def test_resolve_api_key_none_when_empty(monkeypatch):
    monkeypatch.delenv("STILLPOINT_PEXELS_KEY", raising=False)
    from stillpoint import pexels_key as key_mod

    monkeypatch.setattr(key_mod, "PEXELS_API_KEY", "")
    assert pexels.resolve_api_key() is None


# -- search (T007) -------------------------------------------------------------


def test_search_parses_photos_and_strips_query():
    body = _search_body([_photo_payload(1, "one"), _photo_payload(2, "two")])
    photos = pexels.search_images("lake", key="k", fetch=_fake_fetch(body))
    assert len(photos) == 2
    first = photos[0]
    assert first.id == 1
    assert first.alt == "one"
    assert first.photographer == "Jane Doe"
    assert first.width == 4000
    assert first.height == 2250
    assert first.base_url == "https://images.pexels.com/photos/1/pic.jpeg"
    assert "?" not in first.base_url


def test_search_sends_query_default_per_page_and_key():
    fetch = _fake_fetch(_search_body([]))
    pexels.search_images("  lake  ", key="k", fetch=fetch)
    url, headers = fetch.calls[0]
    assert url.startswith("https://api.pexels.com/v1/search?")
    assert "query=lake" in url
    assert "per_page=12" in url
    assert headers == {"Authorization": "k"}


def test_search_honors_custom_per_page():
    fetch = _fake_fetch(_search_body([]))
    pexels.search_images("lake", key="k", per_page=5, fetch=fetch)
    assert "per_page=5" in fetch.calls[0][0]


def test_search_empty_results_are_not_an_error():
    photos = pexels.search_images("nope", key="k", fetch=_fake_fetch(_search_body([])))
    assert photos == []


def test_search_blank_query_returns_empty_without_fetch():
    fetch = _fake_fetch(_search_body([]))
    assert pexels.search_images("   ", key="k", fetch=fetch) == []
    assert fetch.calls == []


def test_search_passes_environment_key(monkeypatch):
    monkeypatch.setenv("STILLPOINT_PEXELS_KEY", "env-key")
    fetch = _fake_fetch(_search_body([]))
    pexels.search_images("lake", fetch=fetch)
    assert fetch.calls[0][1] == {"Authorization": "env-key"}


def test_search_missing_key_raises_classified_other(monkeypatch):
    monkeypatch.delenv("STILLPOINT_PEXELS_KEY", raising=False)
    from stillpoint import pexels_key as key_mod

    monkeypatch.setattr(key_mod, "PEXELS_API_KEY", "")
    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.search_images("lake", key=None)
    assert excinfo.value.kind == "other"
    assert excinfo.value.message == pexels.SEARCH_ERROR_OTHER


def test_search_network_failure_raises_classified():
    def failing(url, headers):
        raise urllib.error.URLError("getaddrinfo failed")

    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.search_images("lake", key="k", fetch=failing)
    assert excinfo.value.kind == "no_connection"
    assert excinfo.value.message == pexels.SEARCH_ERROR_NO_CONNECTION


def test_search_bad_json_raises_other():
    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.search_images("lake", key="k", fetch=_fake_fetch("not json"))
    assert excinfo.value.kind == "other"
    assert excinfo.value.message == pexels.SEARCH_ERROR_OTHER


# -- classify_error (T006 / T026) ----------------------------------------------


def test_classify_search_url_error_is_no_connection():
    assert (
        pexels.classify_error(urllib.error.URLError("getaddrinfo failed"), action="search")
        == pexels.SEARCH_ERROR_NO_CONNECTION
    )


def test_classify_download_url_error_is_no_connection():
    assert (
        pexels.classify_error(urllib.error.URLError("getaddrinfo failed"), action="download")
        == pexels.DOWNLOAD_ERROR_NO_CONNECTION
    )


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("temporary failure in name resolution"),
        TimeoutError("timed out"),
        ConnectionError("connection refused"),
        socket.gaierror("getaddrinfo failed"),
        socket.timeout("timed out"),
        ssl.SSLError("SSL: CERTIFICATE_VERIFY_FAILED"),
    ],
)
def test_classify_network_failures_are_no_connection(exc):
    assert pexels.classify_error(exc, action="search") == pexels.SEARCH_ERROR_NO_CONNECTION
    assert pexels.classify_error(exc, action="download") == pexels.DOWNLOAD_ERROR_NO_CONNECTION
    assert pexels.classify_error(exc, action="preview") == pexels.PREVIEW_ERROR_MESSAGE


@pytest.mark.parametrize("status", [401, 403, 429])
def test_classify_http_errors_are_other(status):
    exc = urllib.error.HTTPError("https://api.pexels.com", status, "Denied", {}, None)
    assert pexels.classify_error(exc, action="search") == pexels.SEARCH_ERROR_OTHER
    assert pexels.classify_error(exc, action="download") == pexels.DOWNLOAD_ERROR_OTHER
    assert pexels.classify_error(exc, action="preview") == pexels.PREVIEW_ERROR_MESSAGE


def test_classify_generic_and_none_are_other():
    assert pexels.classify_error(ValueError("boom"), action="search") == pexels.SEARCH_ERROR_OTHER
    assert pexels.classify_error(ValueError("boom"), action="download") == pexels.DOWNLOAD_ERROR_OTHER
    assert pexels.classify_error(None, action="search") == pexels.SEARCH_ERROR_OTHER


def test_classify_unknown_action_raises():
    with pytest.raises(ValueError):
        pexels.classify_error(ValueError("x"), action="rename")


# -- download_photo (T013, T015) -----------------------------------------------


def test_download_photo_stores_one_1920x1080_jpeg(tmp_path):
    project = _project(tmp_path)
    filename = pexels.download_photo(project, _photo(), fetch=lambda url: _jpeg_bytes())
    assert filename == "A calm lake at sunset-123.jpg"
    media = project.media_dir()
    assert [p for p in media.iterdir() if p.is_file()] == [media / filename]
    with Image.open(media / filename) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.size == (1920, 1080)


def test_download_photo_reuses_existing_file(tmp_path):
    project = _project(tmp_path)
    photo = _photo()
    first = pexels.download_photo(project, photo, fetch=lambda url: _jpeg_bytes())
    second = pexels.download_photo(project, photo, fetch=lambda url: _jpeg_bytes())
    assert first == second
    assert [p.name for p in project.media_dir().iterdir() if p.is_file()] == [first]


def test_download_photo_uses_pexels_id_when_alt_empty(tmp_path):
    project = _project(tmp_path)
    filename = pexels.download_photo(project, _photo(alt=""), fetch=lambda url: _jpeg_bytes())
    assert filename == "pexels-123-123.jpg"


def test_download_photo_reuses_deterministic_name_when_placed(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "A calm lake at sunset-123.jpg").write_bytes(b"other")
    filename = pexels.download_photo(project, _photo(), fetch=lambda url: _jpeg_bytes())
    assert filename == "A calm lake at sunset-123.jpg"
    assert (project.media_dir() / filename).read_bytes() == b"other"  # untouched (FR-012)


def test_download_photo_failure_leaves_nothing(tmp_path):
    project = _project(tmp_path)

    def failing(url):
        raise urllib.error.URLError("no such host")

    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.download_photo(project, _photo(), fetch=failing)
    assert excinfo.value.kind == "no_connection"
    assert excinfo.value.message == pexels.DOWNLOAD_ERROR_NO_CONNECTION
    assert [p.name for p in project.media_dir().iterdir() if p.is_file()] == []
    assert [p.name for p in project.media_dir().iterdir() if p.name.startswith(".stillpoint-img-")] == []


def test_download_photo_bad_bytes_is_other(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.download_photo(project, _photo(), fetch=lambda url: b"not-an-image")
    assert excinfo.value.kind == "other"
    assert [p.name for p in project.media_dir().iterdir() if p.is_file()] == []


# -- preview_photo (T013, T017) ------------------------------------------------


def test_preview_photo_returns_rgb_and_writes_nothing(tmp_path):
    project = _project(tmp_path)
    img = pexels.preview_photo(_photo(), fetch=lambda url: _jpeg_bytes(640, 360))
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"
    assert img.size == (640, 360)
    assert [p.name for p in project.media_dir().iterdir() if p.is_file()] == []


def test_preview_photo_network_failure_is_plain_error(tmp_path):
    def failing(url):
        raise TimeoutError("timed out")

    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.preview_photo(_photo(), fetch=failing)
    assert excinfo.value.kind == "no_connection"
    assert excinfo.value.message == pexels.PREVIEW_ERROR_MESSAGE


def test_preview_photo_bad_bytes_is_plain_error(tmp_path):
    with pytest.raises(pexels.PexelsError) as excinfo:
        pexels.preview_photo(_photo(), fetch=lambda url: b"not-an-image")
    assert excinfo.value.kind == "other"
    assert excinfo.value.message == pexels.PREVIEW_ERROR_MESSAGE


# -- list_downloaded_images (T030) ---------------------------------------------


def test_list_downloaded_images_newest_first_and_extensions(tmp_path):
    project = _project(tmp_path)
    media = project.media_dir()
    _write_image(media, "a.jpg", time.time() - 30)
    _write_image(media, "b.png", time.time() - 10)
    _write_image(media, "c.webp", time.time() - 20)
    _write_image(media, "d.jpeg", time.time() - 40)
    _write_image(media, "e.gif", time.time() - 50)
    _write_image(media, "f.bmp", time.time() - 60)
    (media / "g.m4a").write_bytes(b"x")  # not an image
    (media / "h.txt").write_bytes(b"x")  # not an image
    (media / "sub").mkdir()

    assert pexels.list_downloaded_images(project) == [
        "b.png", "c.webp", "a.jpg", "d.jpeg", "e.gif", "f.bmp",
    ]


def test_list_downloaded_images_empty(tmp_path):
    project = _project(tmp_path)
    assert pexels.list_downloaded_images(project) == []


def test_list_downloaded_images_drops_deleted_file(tmp_path):
    project = _project(tmp_path)
    _write_image(project.media_dir(), "gone.jpg", time.time() - 5)
    assert "gone.jpg" in pexels.list_downloaded_images(project)
    (project.media_dir() / "gone.jpg").unlink()
    assert pexels.list_downloaded_images(project) == []


# -- set_background_image (T004, T013, T023, T030) -----------------------------


def test_set_background_image_writes_single_entry_and_saves(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "lake.jpg").write_bytes(b"x")
    item = project.set_background_image("lake.jpg")
    assert item.kind == "image"
    assert item.filename == "lake.jpg"
    assert item.duration == project.movie.duration  # the still spans the whole film
    loaded = model_mod.Project.load(project.directory)
    assert [i.to_dict() for i in loaded.images] == [item.to_dict()]


def test_set_background_image_same_file_is_noop(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "lake.jpg").write_bytes(b"x")
    project.set_background_image("lake.jpg")
    before = (project.directory / "project.json").read_bytes()
    project.set_background_image("lake.jpg")
    assert (project.directory / "project.json").read_bytes() == before
    assert [i.filename for i in project.images] == ["lake.jpg"]


def test_set_background_image_swaps_while_files_stay(tmp_path):
    project = _project(tmp_path)
    (project.media_dir() / "one.jpg").write_bytes(b"1")
    (project.media_dir() / "two.jpg").write_bytes(b"2")
    project.set_background_image("one.jpg")
    project.set_background_image("two.jpg")
    assert [i.filename for i in project.images] == ["two.jpg"]
    assert sorted(p.name for p in project.media_dir().iterdir() if p.is_file()) == ["one.jpg", "two.jpg"]
    loaded = model_mod.Project.load(project.directory)
    assert [i.filename for i in loaded.images] == ["two.jpg"]


def test_set_background_image_rejects_missing_file(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        project.set_background_image("gone.jpg")
    assert project.images == []


def test_project_without_images_key_loads_and_sets_background(tmp_path):
    project = _project(tmp_path)
    data = json.loads((project.directory / "project.json").read_text())
    del data["images"]
    (project.directory / "project.json").write_text(json.dumps(data))
    loaded = model_mod.Project.load(project.directory)
    assert loaded.images == []
    (loaded.media_dir() / "lake.jpg").write_bytes(b"x")
    loaded.set_background_image("lake.jpg")
    roundtrip = model_mod.Project.load(loaded.directory)
    assert [i.filename for i in roundtrip.images] == ["lake.jpg"]


def test_legacy_images_list_loads_and_can_be_replaced(tmp_path):
    project = _project(tmp_path)
    data = json.loads((project.directory / "project.json").read_text())
    data["images"] = [{"kind": "image", "filename": "old.jpg", "duration": 5.0}]
    (project.directory / "project.json").write_text(json.dumps(data))
    loaded = model_mod.Project.load(project.directory)
    assert [i.filename for i in loaded.images] == ["old.jpg"]
    (loaded.media_dir() / "lake.jpg").write_bytes(b"x")
    loaded.set_background_image("lake.jpg")
    roundtrip = model_mod.Project.load(loaded.directory)
    assert [i.filename for i in roundtrip.images] == ["lake.jpg"]

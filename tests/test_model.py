import json

import pytest

from stillpoint import model


def test_new_project_saves_files(tmp_path):
    project = model.new_project("My Retreat", tmp_path / "proj", "2026-08-13T10:00:00")
    assert project.project_file.exists()
    assert (tmp_path / "proj" / "media").is_dir()
    assert (tmp_path / "proj" / "renders").is_dir()


def test_old_project_json_without_volume_opens_and_plays(tmp_path):
    """T035 / FR-013: a Specs 001–004 project.json (audio items have no
    ``volume`` field — Spec 5 added it) opens unchanged and plays at full
    balance; the loader never invents or requires the new field."""
    proj_dir = tmp_path / "old"
    proj_dir.mkdir()
    (proj_dir / "media").mkdir()
    (proj_dir / "project.json").write_text(json.dumps({
        "version": 1,
        "title": "Old",
        "created": "t0",
        "ratio": "16:9",
        "imageDuration": 5.0,
        "movie": {
            "duration": 10.0,
            "ratio": "16:9",
            "crossfade": 0.0,
            "audio": {
                "kind": "audio", "filename": "song.wav", "duration": 5.0,
                "in_point": 0.0, "fade_in": 0.0, "fade_out": 0.0,
            },
            "voice": None,
        },
        "images": [{"kind": "image", "filename": "a.jpg", "duration": 5.0,
                    "in_point": 0.0, "fade_in": 0.0, "fade_out": 0.0}],
    }))
    project = model.Project.load(proj_dir)
    assert project.movie.audio is not None
    assert project.movie.audio.filename == "song.wav"
    assert project.movie.audio.volume == 1.0  # defaults to full — plays unchanged
    (project.media_dir() / "song.wav").write_bytes(b"present")
    from stillpoint import mix

    plan = mix.plan_audio(project, total=5.0)
    assert plan.has_audio is True
    assert "volume=1.000" in plan.chains[0]


def test_volume_edits_write_only_existing_fields(tmp_path):
    """T035 / FR-013 / Constitution VIII: a volume edit reuses the existing
    ``volume`` field — saving adds no new keys to the project file."""
    project = model.new_project("Vol", tmp_path / "proj", "t0")
    project.movie.audio = model.MediaItem(kind="audio", filename="song.mp3")
    project.set_channel_volume("music", 0.4)
    raw = json.loads((tmp_path / "proj" / "project.json").read_text())
    assert set(raw["movie"].keys()) == {"duration", "ratio", "crossfade", "audio", "voice"}
    assert set(raw["movie"]["audio"].keys()) == {
        "kind", "filename", "duration", "in_point", "volume", "echo", "fade_in", "fade_out"
    }
    assert raw["movie"]["audio"]["volume"] == 0.4


def test_roundtrip_preserves_content(tmp_path):
    project = model.new_project("Zen", tmp_path / "proj", "t0")
    project.movie.duration = 300
    project.images.append(model.MediaItem(kind="image", filename="a.jpg", duration=10.0))
    project.save()

    loaded = model.Project.load(tmp_path / "proj")
    assert loaded.title == "Zen"
    assert loaded.movie.duration == 300
    assert loaded.images[0].filename == "a.jpg"
    assert loaded.directory == tmp_path / "proj"


def test_echo_serializes_and_round_trips(tmp_path):
    """T016 / FR-011/FR-012: the one new field (echo) round-trips through
    project.json — a saved value reappears exactly where she left it."""
    project = model.new_project("Echo", tmp_path / "proj", "t0")
    project.movie.audio = model.MediaItem(kind="audio", filename="a.mp3", echo=0.7)
    project.movie.voice = model.MediaItem(kind="audio", filename="v.wav", echo=0.0)
    project.save()

    loaded = model.Project.load(tmp_path / "proj")
    assert loaded.movie.audio.echo == pytest.approx(0.7)
    assert loaded.movie.voice.echo == 0.0
    assert model.MediaItem.to_dict(loaded.movie.audio)["echo"] == 0.7


def test_all_shaping_metrics_round_trip_through_config_file(tmp_path):
    """R2: every slider's metric — volume, echo, fade_in, fade_out — is
    persisted accurately to project.json and comes back exactly on load, so
    the readout after reopening matches where she left it."""
    project = model.new_project("Shaping", tmp_path / "proj", "t0")
    project.movie.audio = model.MediaItem(
        kind="audio", filename="a.mp3", volume=0.35, echo=0.4, fade_in=2.5, fade_out=6.5)
    project.save()

    raw = json.loads((tmp_path / "proj" / "project.json").read_text())
    stored = raw["movie"]["audio"]
    assert stored["volume"] == pytest.approx(0.35)
    assert stored["echo"] == pytest.approx(0.4)
    assert stored["fade_in"] == pytest.approx(2.5)
    assert stored["fade_out"] == pytest.approx(6.5)

    loaded = model.Project.load(tmp_path / "proj")
    assert loaded.movie.audio.volume == pytest.approx(0.35)
    assert loaded.movie.audio.echo == pytest.approx(0.4)
    assert loaded.movie.audio.fade_in == pytest.approx(2.5)
    assert loaded.movie.audio.fade_out == pytest.approx(6.5)


def test_pre_006_project_opens_with_echo_off(tmp_path):
    """T016 / FR-012: a project saved before this feature (audio items with no
    ``echo`` field) opens unchanged with echo off — defaults, never an error."""
    proj_dir = tmp_path / "old"
    proj_dir.mkdir()
    (proj_dir / "media").mkdir()
    (proj_dir / "project.json").write_text(json.dumps({
        "version": 1,
        "title": "Old",
        "created": "t0",
        "ratio": "16:9",
        "imageDuration": 5.0,
        "movie": {
            "duration": 10.0,
            "ratio": "16:9",
            "crossfade": 0.0,
            "audio": {
                "kind": "audio", "filename": "song.wav", "duration": 5.0,
                "in_point": 0.0, "volume": 1.0, "fade_in": 0.0, "fade_out": 0.0,
            },
            "voice": None,
        },
        "images": [],
    }))
    project = model.Project.load(proj_dir)
    assert project.movie.audio is not None
    assert project.movie.audio.echo == 0.0  # default: every modulation off
    assert project.movie.audio.volume == 1.0  # stored values untouched


def test_validate_rejects_bad_title(tmp_path):
    project = model.Project(title="bad/name", directory=tmp_path)
    assert any("title" in p for p in project.validate())


def test_validate_rejects_long_movie(tmp_path):
    project = model.Project(title="Ok", directory=tmp_path)
    project.movie.duration = 0.5
    assert any("at least 1 second" in p for p in project.validate())


def test_save_raises_on_invalid(tmp_path):
    project = model.Project(title="<bad>", directory=tmp_path)
    with pytest.raises(ValueError):
        project.save()


def test_add_image_copies_file(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg-data")
    project = model.new_project("Proj", tmp_path / "proj", "t0")
    item = project.add_image(source)
    assert item.filename == "photo.jpg"
    assert project.media_file(item).read_bytes() == b"jpeg-data"


def test_add_image_unique_names(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg-data")
    project = model.new_project("Proj", tmp_path / "proj", "t0")
    first = project.add_image(source)
    second = project.add_image(source)
    assert first.filename != second.filename
    assert second.filename == "photo (2).jpg"


def test_share_archive_contains_media(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg-data")
    project = model.new_project("Proj", tmp_path / "proj", "t0")
    project.add_image(source)
    archive = tmp_path / "share.zip"
    project.make_share_archive(archive)

    import zipfile

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "project.json" in names
    assert "media/photo.jpg" in names

import pytest

from stillpoint import model


def test_new_project_saves_files(tmp_path):
    project = model.new_project("My Retreat", tmp_path / "proj", "2026-08-13T10:00:00")
    assert project.project_file.exists()
    assert (tmp_path / "proj" / "media").is_dir()
    assert (tmp_path / "proj" / "renders").is_dir()


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

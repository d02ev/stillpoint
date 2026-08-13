import json

from stillpoint import io


def test_atomic_write_and_read(tmp_path):
    path = tmp_path / "nested" / "data.json"
    io.atomic_write_json(path, {"b": 1, "a": 2})
    assert json.loads(path.read_text()) == {"a": 2, "b": 1}


def test_read_missing_returns_none(tmp_path):
    assert io.read_json(tmp_path / "missing.json") is None


def test_no_leftover_temp_files(tmp_path):
    directory = tmp_path / "io-test"
    directory.mkdir()
    path = directory / "data.json"
    io.atomic_write_text(path, "hello")
    leftovers = [p for p in directory.iterdir() if p.name != "data.json"]
    assert leftovers == []

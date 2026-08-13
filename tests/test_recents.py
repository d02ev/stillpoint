from stillpoint import paths, recents


def test_recents_path_lives_in_appdata(monkeypatch):
    monkeypatch.delenv("STILLPOINT_APPDATA", raising=False)
    assert paths.recents_path().parent == paths.app_data_dir()
    assert "Stillpoint" in str(paths.app_data_dir())


def test_env_override_isolates_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("STILLPOINT_APPDATA", str(tmp_path / "custom"))
    assert paths.app_data_dir() == tmp_path / "custom"


def test_touch_recent_moves_to_top(tmp_path):
    p1 = tmp_path / "one"
    p2 = tmp_path / "two"
    p1.mkdir()
    p2.mkdir()
    recents.touch_recent("One", p1)
    recents.touch_recent("Two", p2)
    recents.touch_recent("One", p1)
    entries = recents.list_recents()
    assert [e["title"] for e in entries][:2] == ["One", "Two"]


def test_remove_recent(tmp_path):
    p1 = tmp_path / "one"
    p1.mkdir()
    recents.touch_recent("One", p1)
    recents.remove_recent(p1)
    assert recents.list_recents() == []


def test_touch_recent_requires_existing_dir(tmp_path):
    import pytest

    with pytest.raises(recents.RecentsError):
        recents.touch_recent("Ghost", tmp_path / "nope")

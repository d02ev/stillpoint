from stillpoint import names


def test_sanitize_strips_forbidden():
    assert names.sanitize_filename('a<b>:"c') == "abc"
    assert names.sanitize_filename("  trailing   spaces  ") == "trailing spaces"


def test_sanitize_empty_becomes_untitled():
    assert names.sanitize_filename("???") == "untitled"


def test_unique_filename_skips_existing(tmp_path):
    (tmp_path / "zen.mp4").write_bytes(b"x")
    assert names.unique_filename(tmp_path, "zen", ".mp4") == "zen (2).mp4"
    assert names.unique_filename(tmp_path, "other", ".mp4") == "other.mp4"


def test_unique_filename_case_insensitive(tmp_path):
    (tmp_path / "zen.mp4").write_bytes(b"x")
    assert names.unique_filename(tmp_path, "Zen", ".MP4") == "Zen (2).MP4"


def test_project_dir_name_truncates():
    long_title = "A" * 200
    assert len(names.project_dir_name(long_title)) == 40

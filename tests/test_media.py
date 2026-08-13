import subprocess

import pytest
from PIL import Image

from stillpoint import media


def _ffmpeg() -> list[str]:
    return [str(media.ffmpeg_path())]


def _make_tone(path, seconds: float = 2.0, freq: int = 440) -> None:
    subprocess.run(
        _ffmpeg()
        + [
            "-v", "error", "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={seconds}",
            "-af", "volume=8.0",
            "-y", str(path),
        ],
        check=True,
    )


def _make_image(path, size=(64, 48), color=(200, 100, 50)) -> None:
    Image.new("RGB", size, color).save(path)


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "tone.wav"
    _make_tone(path)
    return path


def test_ffmpeg_discoverable():
    assert media.ffmpeg_path().is_file()
    assert media.ffprobe_path().is_file()


def test_audio_duration(wav):
    assert media.audio_duration(wav) == pytest.approx(2.0, abs=0.1)


def test_waveform_peaks_shape_and_range(wav):
    peaks = media.waveform_peaks(wav, buckets=100)
    assert len(peaks) == 100
    assert all(0.0 <= p <= 1.0 for p in peaks)
    assert max(peaks) > 0.5


def test_waveform_silence_is_zero(tmp_path):
    silent = tmp_path / "silent.wav"
    subprocess.run(
        _ffmpeg()
        + ["-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", "-y", str(silent)],
        check=True,
    )
    assert media.waveform_peaks(silent, buckets=10) == [0.0] * 10


def test_open_image_rgb(tmp_path):
    path = tmp_path / "img.png"
    _make_image(path)
    image = media.open_image(path)
    assert image.mode == "RGB"


def test_cover_transform_no_bars(tmp_path):
    path = tmp_path / "img.png"
    _make_image(path, size=(64, 48))
    image = media.open_image(path)
    fitted = media.cover_transform(image, 200, 200)
    assert fitted.size == (200, 200)


def test_import_image_writes_jpeg(tmp_path):
    source = tmp_path / "img.png"
    _make_image(source)
    out = tmp_path / "media" / "img.jpg"
    media.import_image(source, out, 320, 180)
    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (320, 180)
        assert img.format == "JPEG"

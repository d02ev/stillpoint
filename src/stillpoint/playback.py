"""Playback of the baked preview mix via the Windows multimedia API.

``WaveOutSink`` streams a 16-bit PCM WAV from disk through ``winmm.dll``
(``ctypes`` — stdlib only, no new dependency, Constitution IX) in small buffers
on a daemon thread, with pause/resume/restart/end-detection (FR-004, research
Decision 3). ``PlaybackSession`` is a display-free controller that drives an
injectable sink and baker through the STOPPED → BAKING → PLAYING ↔ PAUSED →
FINISHED states (research Decision 4); the GUI editor feeds it and never touches
the sink directly. ``classify_playback_error`` maps failures to two plain-
language buckets (FR-011). All user-facing strings are the canonical everyday
strings from contracts/preview-playback-ui.md (Constitution I).
"""

from __future__ import annotations

import ctypes
import shutil
import struct
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path

# -- canonical plain-language strings (contracts/preview-playback-ui.md) --------

UNREADABLE_MESSAGE = "We couldn't read the audio — it may have been moved or deleted."
OTHER_MESSAGE = "Something went wrong starting the preview. Please try again."

# -- session states -------------------------------------------------------------

STOPPED = "STOPPED"
BAKING = "BAKING"
PLAYING = "PLAYING"
PAUSED = "PAUSED"
FINISHED = "FINISHED"

#: ffmpeg/device messages that mean "this isn't audio we can read".
_UNREADABLE_TOKENS = (
    "invalid data found",
    "moov atom",
    "no audio streams",
    "stream 0 is not audio",
    "could not find codec parameters",
    "invalid data",
    "decode failed",
    "error while decoding",
    "malformed file",
    "not a valid audio",
    "unsupported codec",
    "error opening input",
    "cannot open",
    "not a wave",
    "could not read",
)


class PlaybackError(Exception):
    """A playback failure classified for the user: unreadable | other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class WaveOutError(RuntimeError):
    """A failure talking to the Windows audio device."""


def classify_playback_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to exactly one user-facing bucket + plain message.

    Returns ``(kind, message)`` with kind in ``unreadable``, ``other`` (research
    Decision 8). Missing files never reach this path — the mix plan skips them
    into silence (FR-010). Never a raw traceback.
    """
    if isinstance(exc, PlaybackError):
        return exc.kind, exc.message
    if isinstance(exc, (PermissionError, FileNotFoundError, OSError)):
        return "unreadable", UNREADABLE_MESSAGE
    message = str(exc).lower()
    if any(token in message for token in _UNREADABLE_TOKENS):
        return "unreadable", UNREADABLE_MESSAGE
    return "other", OTHER_MESSAGE


def _sink_position_seconds(sink) -> float:
    """Best-effort audible position (s) from an injectable sink; 0 when absent."""
    position = getattr(sink, "position_seconds", None)
    if position is None:
        return 0.0
    try:
        return position()
    except Exception:  # noqa: BLE001 - spot-keeping is best-effort, never fatal
        return 0.0


# -- winmm bindings -------------------------------------------------------------

_WINMM = None


def _winmm():
    """Load winmm.dll lazily and set the waveOut signatures once."""
    global _WINMM
    if _WINMM is None:
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "winmm"):
            raise WaveOutError("audio needs Windows (winmm.dll)")
        dll = ctypes.windll.winmm
        dll.waveOutOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.UINT,
            ctypes.POINTER(_WAVEFORMATEX),
            ctypes.c_void_p,  # dwCallback — DWORD_PTR (64-bit)
            ctypes.c_void_p,  # dwCallbackInstance — DWORD_PTR (64-bit)
            wintypes.DWORD,   # fdwOpen
        ]
        dll.waveOutOpen.restype = wintypes.UINT
        for fn in ("waveOutWrite", "waveOutPrepareHeader", "waveOutUnprepareHeader"):
            getattr(dll, fn).argtypes = [ctypes.c_void_p, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
            getattr(dll, fn).restype = wintypes.UINT
        for fn in ("waveOutPause", "waveOutRestart", "waveOutReset", "waveOutClose"):
            getattr(dll, fn).argtypes = [ctypes.c_void_p]
            getattr(dll, fn).restype = wintypes.UINT
        _WINMM = dll
    return _WINMM


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),  # DWORD_PTR — 8 bytes on 64-bit, keeps dwFlags at offset 24
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),  # DWORD_PTR
    ]


_WAVE_FORMAT_PCM = 0x0001
_WHDR_DONE = 0x00000001
_MMSYSERR_NOERROR = 0
_CALLBACK_FUNCTION = 0x00030000

# The driver posts WOM_DONE and sets WHDR_DONE on each finished buffer only
# when the device was opened with a callback (research Decision 3). The
# callback itself is a no-op — the pump detects completion by polling the
# buffer flags — but its presence is what makes the flags get set at all.
_WAVEOUTCALLBACK = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t
)


@_WAVEOUTCALLBACK
def _winmm_done_callback(_hwo, _msg, _inst, _p1, _p2) -> None:  # noqa: N802 - winmm style
    """No-op: merely keep the callback pointer valid for the device's lifetime."""


def _parse_wav(path: Path) -> tuple[int, int, int, int, int]:
    """Return (channels, rate, bits, data_offset, data_size) of a PCM WAV."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise WaveOutError("not a WAVE audio file")
        while True:
            chunk_head = f.read(8)
            if len(chunk_head) < 8:
                raise WaveOutError("could not read the audio file")
            cid = chunk_head[:4]
            size = struct.unpack("<I", chunk_head[4:8])[0]
            if cid == b"fmt ":
                data = f.read(min(size, 16))
                if len(data) < 16:
                    raise WaveOutError("could not read the audio format")
                tag, channels, rate, _avg, _align, bits = struct.unpack("<HHIIHH", data)
                if tag != _WAVE_FORMAT_PCM or bits != 16:
                    raise WaveOutError("only 16-bit PCM audio can be previewed")
                if size > 16:
                    f.seek(size - 16 + (size % 2), 1)
            elif cid == b"data":
                return channels, rate, bits, f.tell(), size
            else:
                f.seek(size + (size % 2), 1)


class WaveOutSink:
    """Stream a 16-bit PCM WAV through winmm in small buffers on a daemon thread.

    ``open`` reads the WAV and opens the device; ``play`` streams from disk
    (never loading the whole file — a very long mix stays light, Constitution
    II); ``pause``/``resume`` freeze and continue at her exact spot (FR-004);
    ``restart`` returns to byte 0 (Start over); ``done()`` is true when the
    final buffer has finished. CPU is consumed only while actually playing.
    """

    def __init__(self, *, block_bytes: int = 65536, max_buffers: int = 4, poll_s: float = 0.02):
        self._block_bytes = block_bytes
        self._max_buffers = max_buffers
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._hwo: ctypes.c_void_p | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wav_path: Path | None = None
        self._format: tuple[int, int, int] | None = None
        self._data_offset = 0
        self._data_size = 0
        self._pos = 0
        self._in_flight: list = []  # [(WAVEHDR, buffer)] — the buffer keeps the header alive
        self._finished = False
        self._paused = False
        self._opened = False

    # -- control ---------------------------------------------------------------

    def open(self, path, *, start_seconds: float = 0.0) -> None:
        """Load ``path`` and open the device, optionally starting partway in.

        ``start_seconds`` resumes at an approximate position in the audio data
        (the session uses it to keep her spot across a live re-bake); it is
        aligned to a sample boundary and clamped into the file.
        """
        with self._lock:
            self._close_device_locked()
            self._wav_path = Path(path)
            channels, rate, bits, offset, size = _parse_wav(self._wav_path)
            self._format = (channels, rate, bits)
            self._data_offset = offset
            self._data_size = size
            block_align = channels * bits // 8
            bytes_per_sec = rate * block_align
            pos = int(max(0.0, start_seconds) * bytes_per_sec)
            self._pos = (pos // block_align) * block_align
            if self._pos >= size:
                self._pos = 0  # the file ends before her spot: play from the top
            self._in_flight = []
            self._finished = False
            self._paused = False
            self._opened = True
            self._open_device_locked()

    def play(self) -> None:
        """Play from the top (a stopped/finished or fresh file)."""
        with self._lock:
            if not self._opened or self._hwo is None:
                raise WaveOutError("no audio is loaded to play")
            self._finished = False
            self._paused = False
            self._pos = 0
            self._reset_locked()
            self._start_thread_locked()

    def pause(self) -> None:
        with self._lock:
            if not self._opened or self._hwo is None:
                return
            _winmm().waveOutPause(self._hwo)
            self._paused = True
            self._stop_thread_locked()

    def resume(self) -> None:
        """Resume from her exact spot (never the top, FR-004)."""
        with self._lock:
            if not self._opened or self._hwo is None:
                return
            _winmm().waveOutRestart(self._hwo)
            self._paused = False
            self._start_thread_locked()

    def restart(self) -> None:
        """Return to the top and play (Start over, Clarification Q3)."""
        with self._lock:
            if not self._opened or self._hwo is None:
                return
            _winmm().waveOutReset(self._hwo)
            _winmm().waveOutRestart(self._hwo)
            self._paused = False
            self._pos = 0
            self._finished = False
            self._reap_in_flight_locked()
            self._start_thread_locked()

    def done(self) -> bool:
        """True when the final buffer finished (never while paused)."""
        with self._lock:
            if not self._opened or self._paused:
                return False
            return self._finished

    def position_seconds(self) -> float:
        """Approximate audible position in seconds (submitted minus in-flight)."""
        with self._lock:
            if self._format is None:
                return 0.0
            channels, rate, bits = self._format
            bytes_per_sec = rate * channels * (bits // 8)
            in_flight = sum(len(buf) for _hdr, buf in self._in_flight)
            return max(0, self._pos - in_flight) / bytes_per_sec

    def stop(self) -> None:
        """Release the device and stop streaming."""
        with self._lock:
            self._stop_thread_locked()
            self._close_device_locked()
            self._opened = False
            self._finished = False
            self._wav_path = None

    def close(self) -> None:
        self.stop()

    # -- internals (always called with the lock held) --------------------------

    def _open_device_locked(self) -> None:
        channels, rate, bits = self._format
        block_align = channels * bits // 8
        fmt = _WAVEFORMATEX(
            _WAVE_FORMAT_PCM, channels, rate, rate * block_align, block_align, bits, 0
        )
        handle = ctypes.c_void_p()
        callback = ctypes.cast(_winmm_done_callback, ctypes.c_void_p)
        res = _winmm().waveOutOpen(
            ctypes.byref(handle), -1, ctypes.byref(fmt), callback, None, _CALLBACK_FUNCTION
        )
        if res != _MMSYSERR_NOERROR:
            raise WaveOutError(f"the audio device could not be opened (error {res})")
        self._hwo = handle

    def _close_device_locked(self) -> None:
        if self._hwo is not None:
            try:
                _winmm().waveOutReset(self._hwo)
                self._reap_in_flight_locked()
                _winmm().waveOutClose(self._hwo)
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            self._hwo = None
        self._in_flight = []

    def _reset_locked(self) -> None:
        """Return a live device to the top and clear in-flight buffers."""
        if self._hwo is not None:
            _winmm().waveOutReset(self._hwo)
            _winmm().waveOutRestart(self._hwo)
        self._reap_in_flight_locked()

    def _start_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._stream_loop, name="stillpoint-playback", daemon=True)
        self._thread.start()

    def _stop_thread_locked(self) -> None:
        if self._thread is not None:
            self._stop_event.set()
            if self._thread.is_alive() and self._thread is not threading.current_thread():
                self._thread.join(timeout=1.0)
            self._thread = None

    def _reap_in_flight_locked(self) -> None:
        """Unprepare headers the device has finished with (WHDR_DONE)."""
        if self._hwo is None:
            self._in_flight = []
            return
        remaining: list = []
        for hdr, buf in self._in_flight:
            if hdr.dwFlags & _WHDR_DONE:
                try:
                    _winmm().waveOutUnprepareHeader(self._hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
                except Exception:  # noqa: BLE001 - best-effort unprepare
                    pass
            else:
                remaining.append((hdr, buf))
        self._in_flight = remaining

    def _block_align(self) -> int:
        channels, _rate, bits = self._format
        return channels * bits // 8

    def _stream_loop(self) -> None:
        """Daemon thread: read chunks from disk and queue them to the device."""
        mm = _winmm()
        try:
            with self._lock:
                if not self._opened or self._hwo is None or self._wav_path is None:
                    return
                hwo = self._hwo
                block_align = self._block_align()
            with open(self._wav_path, "rb") as f:
                while True:
                    with self._lock:
                        if self._stop_event.is_set() or self._paused:
                            return
                        self._reap_in_flight_locked()
                        if self._pos >= self._data_size and not self._in_flight:
                            self._finished = True
                            return
                        if len(self._in_flight) >= self._max_buffers:
                            wait = True
                        else:
                            wait = False
                    if wait:
                        self._stop_event.wait(self._poll_s)
                        continue
                    f.seek(self._data_offset + self._pos)
                    want = (self._block_bytes // block_align) * block_align
                    chunk = f.read(want)
                    if not chunk:
                        # EOF: all data is queued; keep looping so the top of the
                        # loop reaps finished buffers and sets _finished once the
                        # device has drained the last one.
                        self._stop_event.wait(self._poll_s)
                        continue
                    with self._lock:
                        if self._stop_event.is_set() or self._paused:
                            return
                        self._submit_buffer_locked(mm, hwo, chunk)
                        self._pos += len(chunk)
        except OSError:
            # The file disappeared mid-play (rare): end gracefully, never crash.
            with self._lock:
                self._finished = True

    def _submit_buffer_locked(self, mm, hwo, chunk: bytes) -> None:
        buf = ctypes.create_string_buffer(chunk, len(chunk))
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(chunk)
        res = mm.waveOutPrepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        if res != _MMSYSERR_NOERROR:
            self._stop_event.set()
            raise WaveOutError(f"the audio device rejected playback (error {res})")
        res = mm.waveOutWrite(hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        if res != _MMSYSERR_NOERROR:
            self._stop_event.set()
            raise WaveOutError(f"the audio device rejected playback (error {res})")
        self._in_flight.append((hdr, buf))


class PlaybackSession:
    """The display-free preview playback controller (research Decision 4).

    Drives an injectable sink (default :class:`WaveOutSink`) and baker (default
    ``mix.render_mix``) through the STOPPED → BAKING → PLAYING ↔ PAUSED →
    FINISHED states. The editor feeds it: on a play press it calls
    :meth:`prepare_play`, runs the bake on its worker thread when asked, and
    then calls :meth:`bake_done`. All rules are headless-testable with a fake
    sink and fake baker — no real audio device needed.
    """

    STOPPED = STOPPED
    BAKING = BAKING
    PLAYING = PLAYING
    PAUSED = PAUSED
    FINISHED = FINISHED

    def __init__(self, *, sink=None, baker=None, signature_fn=None):
        from . import mix

        self.sink = sink if sink is not None else WaveOutSink()
        self._baker = baker if baker is not None else mix.render_mix
        self._signature_fn = signature_fn if signature_fn is not None else mix.mix_signature
        self._state = STOPPED
        self._temp_dir = Path(tempfile.mkdtemp(prefix="stillpoint-preview-"))
        self._wav: Path | None = None
        self._baked_for: object | None = None
        self._pending_signature: object | None = None
        self._bake_resume_seconds: float = 0.0

    # -- state -----------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def wav_path(self) -> Path | None:
        """The WAV the next bake should write to (valid while BAKING)."""
        return self._wav

    # -- the editor's play press --------------------------------------------------

    def prepare_play(self, project) -> str:
        """Decide a play press. Returns ``"resume"`` | ``"play"`` | ``"bake"``.

        ``"resume"`` (from PAUSED with an unchanged mix) and ``"play"`` (baked
        file unchanged) are completed here — the sink is resumed/played and the
        session is PLAYING. A resume whose settings changed re-bakes instead of
        resuming the stale mix (FR-015): the new balance is always heard the
        next time playback starts. ``"bake"`` enters BAKING; the editor must run
        the bake on its worker thread to :attr:`wav_path` and then call
        :meth:`bake_done`.
        """
        signature = self._signature_fn(project)
        if self._state == PAUSED and self._baked_for == signature:
            self.sink.resume()
            self._state = PLAYING
            return "resume"
        if self._wav is None or self._baked_for != signature:
            self._pending_signature = signature
            self._wav = self._temp_dir / "preview.wav"
            self._bake_resume_seconds = 0.0
            self._state = BAKING
            return "bake"
        self.sink.open(str(self._wav))
        self.sink.play()
        self._state = PLAYING
        return "play"

    def settings_changed(self, project) -> str | None:
        """A stored setting changed mid-session: re-bake and keep her spot.

        Returns ``"bake"`` when the change must be applied now (the editor runs
        the baker to :attr:`wav_path` and then calls :meth:`bake_done`), or
        ``None`` when nothing is playing or the baked file already matches — the
        change is then simply picked up on the next play press. Playback stops
        while the re-bake runs; on completion the sink reopens at the audible
        position she had reached (from the top when the mix had finished).
        """
        if self._state not in (PLAYING, PAUSED, FINISHED):
            return None
        signature = self._signature_fn(project)
        if self._wav is not None and self._baked_for == signature:
            return None
        self._pending_signature = signature
        self._wav = self._temp_dir / "preview.wav"
        self._bake_resume_seconds = _sink_position_seconds(self.sink) if self._state != FINISHED else 0.0
        self._state = BAKING
        self.sink.stop()
        return "bake"

    def needs_rebake(self, project) -> bool:
        """True when the baked mix no longer matches the current settings.

        The editor checks this right after :meth:`bake_done` so a setting that
        changed while the bake was running is applied immediately rather than
        silently held back until the next play press.
        """
        if self._state not in (PLAYING, PAUSED, FINISHED):
            return False
        if self._wav is None:
            return True
        return self._signature_fn(project) != self._baked_for

    def bake_done(self, wav_path=None) -> None:
        """The bake finished; open the sink and start playing."""
        if self._state != BAKING:
            return
        if wav_path is not None:
            self._wav = Path(wav_path)
        self._baked_for = self._pending_signature
        self._pending_signature = None
        start_seconds = self._bake_resume_seconds
        self._bake_resume_seconds = 0.0
        self.sink.open(str(self._wav), start_seconds=start_seconds)
        self.sink.play()
        self._state = PLAYING

    def bake_failed(self) -> None:
        """The bake failed; return to a stopped state (control shows play again)."""
        self._state = STOPPED
        self._pending_signature = None

    def play(self, project, wav_path=None) -> None:
        """Synchronous play orchestration (unit tests / non-worker callers).

        Bakes synchronously through the injected baker when the signature
        changed, then plays. The editor uses :meth:`prepare_play` +
        :meth:`bake_done` instead so the bake runs on its worker thread.
        """
        action = self.prepare_play(project)
        if action == "bake":
            path = Path(wav_path) if wav_path is not None else self._wav
            self._baker(project, path)
            self.bake_done(path)

    # -- transport ---------------------------------------------------------------

    def pause(self) -> None:
        if self._state == PLAYING:
            self.sink.pause()
            self._state = PAUSED

    def start_over(self) -> None:
        """Discard the paused position and play from the top (Clarification Q3)."""
        if self._state in (PLAYING, PAUSED, FINISHED):
            self.sink.restart()
            self._state = PLAYING

    def check_finished(self) -> bool:
        """While PLAYING, become FINISHED when the sink reports the end."""
        if self._state == PLAYING and self.sink.done():
            self._state = FINISHED
            return True
        return False

    def stop(self) -> None:
        """Close the sink and delete the temp WAV (leave/close, FR-012)."""
        try:
            self.sink.stop()
        finally:
            self._state = STOPPED
            self._baked_for = None
            self._pending_signature = None
            self._bake_resume_seconds = 0.0
            if self._wav is not None:
                try:
                    self._wav.unlink()
                except OSError:
                    pass
            self._wav = None
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except OSError:
                pass

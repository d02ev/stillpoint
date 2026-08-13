# Stillpoint

A tiny, gentle desktop studio for making meditation videos — a sequence of
still images over an ambient audio bed, with crossfades, trimmed and faded
in/out, exported to a single mp4.

## What it does

- **Project home** — a welcome screen listing your recent projects, with New /
  Open / Remove actions. Projects live in `Documents\Stillpoint Projects`.
- **Editor** — a preview canvas, a thumbnail timeline, and a control panel:
  - add still images (auto-imported, cover-cropped to the film's aspect ratio)
  - reorder / remove images
  - per-movie settings: default image length, crossfade seconds, aspect ratio
    (16:9, 1:1, 9:16)
  - ambient audio with volume and fade-in/fade-out, plus a live waveform
  - **Export…** renders the film in a background thread with a progress bar
- **Rendering** — ffmpeg builds the movie: stills on a fixed canvas, crossfades
  between consecutive images, and the audio bed trimmed to the film length,
  faded at the edges. Output lands in the project's `renders/` folder.

## Requirements

- Windows 10/11 (a Windows environment is assumed)
- Python 3.12+
- [ffmpeg + ffprobe](https://ffmpeg.org/download.html) on `PATH`, or point at
  them with the `STILLPOINT_FFMPEG_DIR` environment variable

## Run from source

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync              # creates .venv and installs stillpoint + dev tools
uv run stillpoint    # launches the app
```

Without uv: `pip install -e .` then `python -m stillpoint`.

## Tests

```bash
uv run pytest
```

All 47 tests run headless (Tk roots are withdrawn; media is generated on the
fly by ffmpeg and Pillow).

## Build the Windows exe

```bash
uv run build-exe
```

Produces a self-contained bundle at `dist/Stillpoint/Stillpoint.exe`
(PyInstaller `--onedir`; the launcher is `run_stillpoint.py`, spec in
`stillpoint.spec`).

## How a project is stored

A project is a folder with:

```
My Retreat/
├── project.json      # the whole movie description (v1)
├── media/            # imported stills (cover-cropped JPGs) + ambient audio
└── renders/          # exported mp4s
```

`project.json` is written atomically on every change, so a crash never leaves a
corrupt file.

## Layout

| Path | Purpose |
| --- | --- |
| `src/stillpoint/model.py` | `Project` / `Movie` / `MediaItem` model, validation, save/load, zip-share |
| `src/stillpoint/media.py` | Pillow image import + ffmpeg duration/waveform probing |
| `src/stillpoint/render.py` | timeline → ffmpeg command builder + render driver |
| `src/stillpoint/gui/` | tkinter app: `app.py`, `home.py`, `editor.py`, `preview.py`, `timeline.py`, `waveform.py`, `workers.py` |
| `src/stillpoint/paths.py` | on-disk locations (overridable via env vars for tests) |
| `src/stillpoint/recents.py` | recent-projects history (`%APPDATA%\Stillpoint\recents.json`) |

Environment variables: `STILLPOINT_PROJECTS_DIR`, `STILLPOINT_APPDATA`,
`STILLPOINT_FFMPEG_DIR` (the first two exist so tests stay out of your real
folders).

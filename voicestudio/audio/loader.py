"""
Audio file import — converts any common audio format into a WAV path that
voicestudio's existing injection pipeline (wav_to_patch) can consume.

Format strategy:
- WAV: passes through untouched (cheapest).
- FLAC, OGG/Vorbis, AIFF, AU: decoded via `soundfile` (already a project
  dependency; built on libsndfile, no external binaries needed).
- MP3, M4A, AAC: decoded via ffmpeg (bundled as a Python wheel through
  `imageio-ffmpeg`, so no system-wide install is required).

We raise an AudioLoadError with actionable text rather than printing or
silently failing, so the UI can surface it.
"""

import tempfile
from pathlib import Path

WAV_SUFFIXES         = {".wav", ".wave"}
SOUNDFILE_SUFFIXES   = {".flac", ".ogg", ".oga", ".aiff", ".aif", ".au"}
FFMPEG_SUFFIXES      = {".mp3", ".m4a", ".aac"}
SUPPORTED_SUFFIXES   = WAV_SUFFIXES | SOUNDFILE_SUFFIXES | FFMPEG_SUFFIXES


class AudioLoadError(RuntimeError):
    pass


def load_to_wav(src_path: Path) -> tuple[Path, bool]:
    """
    Return (wav_path, is_temp).
    - WAV → returned unchanged (is_temp=False).
    - Anything else → decoded to a temp WAV (is_temp=True).
    """
    src = Path(src_path)
    suffix = src.suffix.lower()

    if suffix in WAV_SUFFIXES:
        return src, False

    if suffix in SOUNDFILE_SUFFIXES:
        return _decode_with_soundfile(src), True

    if suffix in FFMPEG_SUFFIXES:
        return _decode_with_ffmpeg(src), True

    raise AudioLoadError(
        f"Unsupported audio format: {suffix or '(no extension)'}.\n"
        f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _decode_with_soundfile(src: Path) -> Path:
    """libsndfile-backed decode — handles FLAC, OGG, AIFF, AU natively."""
    try:
        import soundfile as sf
    except ImportError as e:  # soundfile is in requirements.txt
        raise AudioLoadError(
            "soundfile package is missing.  Run: pip install soundfile"
        ) from e

    try:
        data, samplerate = sf.read(str(src))
    except Exception as e:
        raise AudioLoadError(f"Failed to decode {src.name}: {e}") from e

    tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="vs_import_")[1])
    sf.write(str(tmp), data, samplerate, subtype="PCM_16")
    return tmp


def _decode_with_ffmpeg(src: Path) -> Path:
    """
    MP3/M4A/AAC decode — shells out to ffmpeg directly.

    We prefer the static binary from `imageio-ffmpeg` (a pip-installable
    wheel) so the user doesn't have to install ffmpeg system-wide.  If
    that wheel is missing we fall back to whatever ffmpeg is on PATH.

    We invoke ffmpeg directly (not via pydub) because pydub's
    `AudioSegment.from_file` calls ffprobe to read metadata, and
    imageio-ffmpeg only ships ffmpeg.exe — not ffprobe.exe.
    """
    import subprocess

    ffmpeg_exe = _find_ffmpeg()
    if not ffmpeg_exe:
        raise AudioLoadError(
            "MP3/M4A/AAC import needs ffmpeg.\n"
            "Easiest:  pip install imageio-ffmpeg\n"
            "Or install ffmpeg system-wide from https://ffmpeg.org "
            "and ensure ffmpeg is on PATH."
        )

    tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="vs_import_")[1])
    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-y", "-loglevel", "error",
             "-i", str(src),
             "-ac", "1",         # downmix to mono
             "-c:a", "pcm_s16le",
             str(tmp)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as e:
        try: tmp.unlink()
        except OSError: pass
        raise AudioLoadError(f"Couldn't run ffmpeg ({ffmpeg_exe}): {e}") from e

    if proc.returncode != 0:
        try: tmp.unlink()
        except OSError: pass
        msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise AudioLoadError(
            f"ffmpeg failed to decode {src.name}.\n\n{msg or '(no stderr)'}"
        )
    return tmp


def _find_ffmpeg() -> str | None:
    """Return a path to an ffmpeg binary, or None if none is available."""
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception:
        pass

    # Fall back to whatever's on PATH.
    import shutil as _shutil
    return _shutil.which("ffmpeg")

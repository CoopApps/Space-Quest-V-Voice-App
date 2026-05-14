"""
Audio file import — converts any common audio format into a WAV path that
voicestudio's existing injection pipeline (wav_to_patch) can consume.

Format strategy:
- WAV: passes through untouched (cheapest).
- FLAC, OGG/Vorbis, AIFF, AU: decoded via `soundfile` (already a project
  dependency; built on libsndfile, no external binaries needed).
- MP3, M4A, AAC: decoded via `pydub` + ffmpeg (requires ffmpeg on PATH).

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
        return _decode_with_pydub(src), True

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


def _decode_with_pydub(src: Path) -> Path:
    """pydub + ffmpeg — only needed for MP3/M4A/AAC."""
    try:
        from pydub import AudioSegment
    except ImportError as e:
        raise AudioLoadError(
            "MP3/M4A/AAC import requires the 'pydub' package.\n"
            "Install it with:  pip install pydub\n"
            "You also need ffmpeg on your PATH (https://ffmpeg.org)."
        ) from e

    try:
        segment = AudioSegment.from_file(src)
    except Exception as e:
        raise AudioLoadError(
            f"Failed to decode {src.name}.\n"
            f"This format needs ffmpeg.  Make sure ffmpeg is installed "
            f"and on PATH (https://ffmpeg.org/download.html).\n\n"
            f"Underlying error: {e}"
        ) from e

    tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="vs_import_")[1])
    segment.export(tmp, format="wav")
    return tmp

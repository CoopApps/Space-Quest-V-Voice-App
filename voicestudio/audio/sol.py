"""
SCI1.1 audio patch file encoder/decoder.

Patch file format (audio36 resources):
  [0]  0x8D  — SOL ID byte
  [1]  0x00  — bShift = 0  (audio data starts at byte 2)
  [2+] RIFF WAV data (standard PCM WAV)

The SCI engine reads byte[1] as shift, seeks to (shift+2), and on finding
"RIFF" treats the remainder as a WAV file. This is the format used by all
Freddy Pharkas audio36 patch files and supported by ScummVM.

Target audio: 11025 Hz, 8-bit unsigned, mono. SCI engines accept higher
quality but 11025/8-bit is authentic to the era and keeps files small.
"""

import io
import struct
import wave
from pathlib import Path

import numpy as np

SCI_SAMPLE_RATE  = 11025
PATCH_PREAMBLE   = bytes([0x8D, 0x00])   # SOL ID + shift=0

# For ADPCM decode of existing SOL files (optional, decoder only)
_DPCM8_TABLE = [0, 1, 2, 3, 6, 10, 15, 21]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_wav_as_float(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read WAV → float32 mono samples in [-1, 1], return (samples, rate)."""
    with wave.open(str(wav_path), "rb") as w:
        n_channels   = w.getnchannels()
        sample_width = w.getsampwidth()
        sample_rate  = w.getframerate()
        raw          = w.readframes(w.getnframes())

    if sample_width == 1:
        s = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        s = (s - 128.0) / 128.0
    elif sample_width == 2:
        s = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        s = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width} bytes")

    if n_channels > 1:
        s = s.reshape(-1, n_channels).mean(axis=1)
    return s, sample_rate


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(src_rate, dst_rate)
        return resample_poly(samples, dst_rate // g, src_rate // g).astype(np.float32)
    except ImportError:
        n_out = int(len(samples) * dst_rate / src_rate)
        return np.interp(
            np.linspace(0, len(samples) - 1, n_out),
            np.arange(len(samples)),
            samples,
        ).astype(np.float32)


def _float_to_uint8(samples: np.ndarray) -> bytes:
    pcm = ((np.clip(samples, -1.0, 1.0) + 1.0) * 127.5).astype(np.uint8)
    return pcm.tobytes()


def _make_wav_bytes(pcm_data: bytes, sample_rate: int) -> bytes:
    """Pack raw 8-bit PCM into a RIFF WAV byte string."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(sample_rate)
        w.writeframes(pcm_data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def wav_to_patch(wav_path: Path, out_path: Path,
                 target_rate: int = SCI_SAMPLE_RATE) -> None:
    """
    Convert any WAV to a SCI audio36 patch file (0x8D 0x00 + RIFF WAV).
    Resamples to target_rate Hz, converts to 8-bit unsigned mono.
    """
    samples, src_rate = _read_wav_as_float(wav_path)
    if src_rate != target_rate:
        samples = _resample(samples, src_rate, target_rate)
    pcm_data = _float_to_uint8(samples)
    wav_bytes = _make_wav_bytes(pcm_data, target_rate)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(PATCH_PREAMBLE)
        f.write(wav_bytes)


def patch_to_wav(patch_path: Path, out_path: Path) -> None:
    """
    Extract audio from a SCI audio36 patch file back to a plain WAV.
    Handles both RIFF-in-SOL-wrapper and raw SOL ADPCM formats.
    """
    with open(patch_path, "rb") as f:
        data = f.read()

    if len(data) < 4:
        raise ValueError(f"File too short: {patch_path}")

    # Detect format
    if data[0] == 0x8D and data[2:6] == b"RIFF":
        # RIFF WAV wrapped with 2-byte SOL preamble
        wav_data = data[2:]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(wav_data)
        return

    if data[2:6] == b"SOL\x00":
        # Raw SOL format
        shift       = data[1]
        audio_start = shift + 2
        sample_rate = struct.unpack_from("<H", data, 6)[0]
        flags       = data[8]
        audio_data  = data[audio_start:]

        if flags & 0x01:   # DPCM compressed
            audio_data = _decode_dpcm8(audio_data)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(1)
            w.setframerate(sample_rate)
            w.writeframes(audio_data)
        return

    # Plain RIFF WAV (no preamble)
    if data[:4] == b"RIFF":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return

    raise ValueError(f"Unrecognised audio patch format in {patch_path}: {data[:8].hex()}")


def _decode_dpcm8(data: bytes) -> bytes:
    """Decode 8-bit SOL ADPCM (new INDEX4 = code & 7 variant)."""
    import array as _array
    cur = 0x80
    out = _array.array("B")
    for byte in data:
        for nibble in (byte >> 4, byte & 0x0F):
            delta = _DPCM8_TABLE[nibble & 7]
            cur = max(0, min(255, cur - delta if nibble & 8 else cur + delta))
            out.append(cur)
    return bytes(out)


def verify_roundtrip(wav_path: Path) -> bool:
    """
    Encode WAV → patch file → decode back to WAV, check fidelity.
    Returns True if mean absolute error < 5% (8-bit quantisation tolerance).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        patch = Path(tmp) / "test.aud"
        check = Path(tmp) / "check.wav"
        wav_to_patch(wav_path, patch)
        patch_to_wav(patch, check)

        orig, orig_rate    = _read_wav_as_float(wav_path)
        decoded, dec_rate  = _read_wav_as_float(check)

        if orig_rate != dec_rate:
            orig = _resample(orig, orig_rate, dec_rate)

        n = min(len(orig), len(decoded))
        mae = float(np.abs(orig[:n] - decoded[:n]).mean())
        print(f"  Sample rate: {dec_rate} Hz")
        print(f"  Samples:     {n}")
        print(f"  MAE:         {mae:.4f}  (< 0.05 = PASS)")
        return mae < 0.05


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    usage = (
        "Usage:\n"
        "  sol.py encode <input.wav> <output.aud>   — WAV → SCI patch\n"
        "  sol.py decode <input.aud> <output.wav>   — SCI patch → WAV\n"
        "  sol.py verify <input.wav>                — roundtrip test\n"
        "  sol.py inspect <input.aud>               — show header info\n"
    )

    if len(sys.argv) < 3:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "encode":
        wav_to_patch(Path(sys.argv[2]), Path(sys.argv[3]))
        print(f"Encoded {sys.argv[2]} → {sys.argv[3]}")
    elif cmd == "decode":
        patch_to_wav(Path(sys.argv[2]), Path(sys.argv[3]))
        print(f"Decoded {sys.argv[2]} → {sys.argv[3]}")
    elif cmd == "verify":
        ok = verify_roundtrip(Path(sys.argv[2]))
        print("PASS" if ok else "FAIL")
    elif cmd == "inspect":
        with open(sys.argv[2], "rb") as f:
            d = f.read(20)
        print(f"Bytes 0-19: {d.hex(' ')}")
        print(f"ID:    0x{d[0]:02X}")
        print(f"Shift: {d[1]}  (data starts at {d[1]+2})")
        if d[2:6] == b"RIFF":
            print("Format: RIFF WAV (wrapped)")
        elif d[2:6] == b"SOL\x00":
            sr = struct.unpack_from("<H", d, 6)[0]
            print(f"Format: SOL  rate={sr}  flags=0x{d[8]:02X}")
    else:
        print(usage)
        sys.exit(1)

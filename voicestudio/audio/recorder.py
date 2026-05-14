"""
Microphone recording and audio playback for the Voice Studio.

Wraps sounddevice for capture and playback.
All recordings are stored as standard WAV files; SOL conversion
happens separately in sol.py when deploying to the game.
"""

import io
import queue
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

DEFAULT_SAMPLE_RATE = 44100   # Record at high quality; downsampled to 11025 on deploy
DEFAULT_CHANNELS    = 1
DEFAULT_DTYPE       = "int16"


class Recorder:
    """
    Thread-safe microphone recorder.

    Usage:
        rec = Recorder()
        rec.start(device_id=0)
        ... user speaks ...
        wav_bytes = rec.stop()   # returns WAV file bytes
    """

    def __init__(self,
                 sample_rate: int = DEFAULT_SAMPLE_RATE,
                 channels: int = DEFAULT_CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels    = channels
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Device enumeration
    # ------------------------------------------------------------------

    @staticmethod
    def list_input_devices() -> list[dict]:
        """Return list of available input devices as dicts."""
        devices = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append({
                    "id":       i,
                    "name":     d["name"],
                    "channels": d["max_input_channels"],
                    "rate":     int(d["default_samplerate"]),
                })
        return devices

    @staticmethod
    def default_input_device() -> int | None:
        try:
            idx = sd.default.device[0]
            return idx if idx >= 0 else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def start(self, device_id: int | None = None,
              level_callback: Callable[[float], None] | None = None) -> None:
        """Begin recording from the given device (None = system default)."""
        if self._recording:
            return

        self._chunks = []
        self._recording = True

        def _callback(indata: np.ndarray, frames: int,
                      time_info, status) -> None:
            chunk = indata.copy()
            self._chunks.append(chunk)
            if level_callback is not None:
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                level_callback(rms / 32768.0)   # normalised 0-1

        self._stream = sd.InputStream(
            device=device_id,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DEFAULT_DTYPE,
            callback=_callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        """
        Stop recording and return the captured audio as WAV bytes.
        """
        if not self._recording:
            return b""

        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._chunks:
            return b""

        audio = np.concatenate(self._chunks, axis=0)
        return _array_to_wav(audio, self.sample_rate, self.channels)

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    @staticmethod
    def play_wav(wav_path: Path,
                 device_id: int | None = None,
                 blocking: bool = False) -> None:
        """Play a WAV file. Returns immediately unless blocking=True."""
        with wave.open(str(wav_path), "rb") as w:
            rate     = w.getframerate()
            n_ch     = w.getnchannels()
            sw       = w.getsampwidth()
            frames   = w.readframes(w.getnframes())

        if sw == 1:
            audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
            audio = (audio - 128.0) / 128.0
        elif sw == 2:
            audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        else:
            audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0

        if n_ch > 1:
            audio = audio.reshape(-1, n_ch)

        sd.play(audio, samplerate=rate, device=device_id)
        if blocking:
            sd.wait()

    @staticmethod
    def play_wav_bytes(wav_bytes: bytes,
                       device_id: int | None = None) -> None:
        """Play WAV from bytes (e.g., just-recorded audio before saving)."""
        tmp = io.BytesIO(wav_bytes)
        with wave.open(tmp, "rb") as w:
            rate   = w.getframerate()
            n_ch   = w.getnchannels()
            sw     = w.getsampwidth()
            frames = w.readframes(w.getnframes())

        if sw == 1:
            audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
            audio = (audio - 128.0) / 128.0
        elif sw == 2:
            audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        else:
            audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0

        if n_ch > 1:
            audio = audio.reshape(-1, n_ch)
        sd.play(audio, samplerate=rate, device=device_id)

    @staticmethod
    def stop_playback() -> None:
        sd.stop()

    # ------------------------------------------------------------------
    # Waveform data
    # ------------------------------------------------------------------

    @staticmethod
    def get_waveform(wav_path: Path, n_points: int = 800) -> np.ndarray:
        """
        Return a downsampled waveform array (n_points values, range [-1, 1])
        suitable for drawing in the UI.
        """
        with wave.open(str(wav_path), "rb") as w:
            sw     = w.getsampwidth()
            frames = w.readframes(w.getnframes())

        if sw == 1:
            audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
            audio = (audio - 128.0) / 128.0
        elif sw == 2:
            audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        else:
            audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0

        if len(audio) == 0:
            return np.zeros(n_points)

        # Chunk into n_points segments, take max absolute value per chunk
        chunk_size = max(1, len(audio) // n_points)
        n_chunks   = len(audio) // chunk_size
        trimmed    = audio[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
        envelope   = np.abs(trimmed).max(axis=1)

        # Pad or trim to exactly n_points
        if len(envelope) < n_points:
            envelope = np.pad(envelope, (0, n_points - len(envelope)))
        else:
            envelope = envelope[:n_points]
        return envelope

    @staticmethod
    def get_duration(wav_path: Path) -> float:
        """Return duration of a WAV file in seconds."""
        with wave.open(str(wav_path), "rb") as w:
            return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _array_to_wav(audio: np.ndarray, sample_rate: int, channels: int) -> bytes:
    """Convert a numpy int16 array to WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)   # int16 = 2 bytes
        w.setframerate(sample_rate)
        w.writeframes(audio.tobytes())
    return buf.getvalue()


def save_wav(wav_bytes: bytes, path: Path) -> None:
    """Write WAV bytes to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)


if __name__ == "__main__":
    print("Available input devices:")
    for d in Recorder.list_input_devices():
        marker = " <-- default" if d["id"] == Recorder.default_input_device() else ""
        print(f"  [{d['id']}] {d['name']}  ({d['channels']}ch @ {d['rate']}Hz){marker}")

"""
Repackage voice recordings into AUDIO/RESOURCE.AUD + AUDIO/N.MAP files.

Layout (matching Freddy Pharkas / SCI1.1 talkie structure):
  <game_dir>/
    RESOURCE.AUD        ← existing sfx (untouched)
    AUDIO/
      RESOURCE.AUD      ← our speech clips (SOL format, concatenated)
      0.MAP             ← per-module audio map for room 0
      200.MAP           ← per-module audio map for room 200
      ...

AUDIO/RESOURCE.AUD format:
  Concatenated SOL clips, each:
    [0x8D][shift][SOL\0][sample_rate uint16][flags byte][data_size uint32][pcm_data]

Per-module AUDIO/N.MAP format (late SCI1.1):
  [baseOffset uint32 LE]           offset of first clip in RESOURCE.AUD
  For each clip in this module (sorted by noun,verb,cond,seq):
    [noun  byte]
    [verb  byte]
    [cond  byte]
    [seq   byte]                   high bits reserved for sync flags (unused here)
    [cumOff 3 bytes LE]            delta from previous clip offset
  [0xFF * 10]                      terminator
"""

import struct
from collections import defaultdict
from pathlib import Path

from voicestudio.audio.sol import wav_to_patch, PATCH_PREAMBLE, SCI_SAMPLE_RATE
from voicestudio.packer.audiocache import AudioCache

SOL_ID    = 0x8D
SOL_SHIFT = 0x0B          # 13-byte header: data starts at shift+2 = 13
SOL_MAGIC = b"SOL\x00"
MAP_MAGIC      = bytes([0x90, 0x00])   # SCI1.1 audio map identifier
MAP_TERMINATOR = bytes([0xFF] * 11)


def _make_sol_clip(pcm_bytes: bytes, sample_rate: int = SCI_SAMPLE_RATE) -> bytes:
    """
    Wrap raw 8-bit unsigned PCM bytes in a SOL header.
    Returns the complete SOL clip (header + data).
    """
    header = (
        bytes([SOL_ID, SOL_SHIFT])
        + SOL_MAGIC
        + struct.pack("<H", sample_rate)
        + bytes([0x00])                 # flags: uncompressed 8-bit PCM
        + struct.pack("<I", len(pcm_bytes))
    )
    return header + pcm_bytes


def _read_pcm_from_patch(patch_path: Path) -> bytes:
    """
    Extract raw 8-bit unsigned PCM from a SCI patch file (0x8D 0x00 + RIFF WAV).
    """
    import wave, io, numpy as np
    from voicestudio.audio.sol import _read_wav_as_float, _resample, _float_to_uint8

    data = patch_path.read_bytes()
    if data[:2] == PATCH_PREAMBLE and data[2:6] == b"RIFF":
        # Strip the 2-byte preamble and read as WAV
        buf = io.BytesIO(data[2:])
        with wave.open(buf, "rb") as w:
            rate   = w.getframerate()
            n_ch   = w.getnchannels()
            sw     = w.getsampwidth()
            frames = w.readframes(w.getnframes())

        if sw == 1:
            samples = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
            samples = (samples - 128.0) / 128.0
        elif sw == 2:
            samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        else:
            samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0

        if n_ch > 1:
            samples = samples.reshape(-1, n_ch).mean(axis=1)
        if rate != SCI_SAMPLE_RATE:
            samples = _resample(samples, rate, SCI_SAMPLE_RATE)
        return _float_to_uint8(samples)

    raise ValueError(f"Unsupported patch format: {patch_path}")


def _write_map_file(map_path: Path,
                    entries: list[tuple[int, int, int, int, int]]) -> None:
    """
    Write a per-module MAP file.

    entries: list of (noun, verb, cond, seq, absolute_offset_in_resource_aud)
             sorted by (noun, verb, cond, seq)
    """
    if not entries:
        return

    # baseOffset = absolute offset of the first entry's clip in RESOURCE.AUD
    base_offset = entries[0][4]

    map_path.parent.mkdir(parents=True, exist_ok=True)
    with open(map_path, "wb") as f:
        f.write(MAP_MAGIC + struct.pack("<I", base_offset))

        prev_abs = base_offset
        for noun, verb, cond, seq, abs_offset in sorted(entries):
            cum_off = abs_offset - prev_abs
            if cum_off < 0:
                raise ValueError(
                    f"Negative cumulative offset for {noun},{verb},{cond},{seq}: "
                    f"abs={abs_offset} prev={prev_abs}"
                )
            # Write 3-byte little-endian cumulative offset
            cum_bytes = struct.pack("<I", cum_off)[:3]
            f.write(bytes([noun, verb, cond, seq]) + cum_bytes)
            prev_abs = abs_offset

        f.write(MAP_TERMINATOR)


def repackage(game_dir: Path, audiocache: AudioCache,
              dry_run: bool = False) -> dict:
    """
    Build AUDIO/RESOURCE.AUD and AUDIO/N.MAP files from the audiocache.

    Returns a summary dict: {modules_written, clips_written, aud_size_bytes}
    """
    game_dir  = Path(game_dir)
    audio_dir = game_dir / "AUDIO"
    aud_path  = audio_dir / "RESOURCE.AUD"

    audio_dir.mkdir(exist_ok=True)

    # Gather all recordings from audiocache
    recordings = audiocache.all_recordings()
    if not recordings:
        return {"modules_written": 0, "clips_written": 0, "aud_size_bytes": 0}

    # Group by module
    by_module: dict[int, list] = defaultdict(list)
    for rec in recordings:
        by_module[rec["module"]].append(rec)

    # First pass: build RESOURCE.AUD (concatenate all SOL clips)
    # Sort globally by (module, noun, verb, cond, seq) so clips are in a
    # predictable order within the file
    all_recs = sorted(
        recordings,
        key=lambda r: (r["module"], r["noun"], r["verb"], r["cond"], r["seq"]),
    )

    abs_offset = 0
    clip_offsets: dict[tuple, int] = {}   # (mod,n,v,c,s) -> offset in RESOURCE.AUD
    clips_written = 0

    if not dry_run:
        aud_file = open(aud_path, "wb")
    try:
        for rec in all_recs:
            key = (rec["module"], rec["noun"], rec["verb"],
                   rec["cond"], rec["seq"])
            patch_path = rec["path"]

            try:
                pcm = _read_pcm_from_patch(patch_path)
            except Exception as e:
                print(f"  Skipping {patch_path.name}: {e}")
                continue

            sol_clip = _make_sol_clip(pcm)
            clip_offsets[key] = abs_offset

            if not dry_run:
                aud_file.write(sol_clip)

            abs_offset += len(sol_clip)
            clips_written += 1
    finally:
        if not dry_run:
            aud_file.close()

    aud_size = abs_offset

    # Second pass: write per-module MAP files
    modules_written = 0
    for module, recs in by_module.items():
        entries = []
        for rec in recs:
            key = (rec["module"], rec["noun"], rec["verb"],
                   rec["cond"], rec["seq"])
            if key in clip_offsets:
                entries.append((
                    rec["noun"], rec["verb"], rec["cond"], rec["seq"],
                    clip_offsets[key],
                ))

        if not entries:
            continue

        map_path = audio_dir / f"{module}.MAP"
        if not dry_run:
            _write_map_file(map_path, entries)
        modules_written += 1

    return {
        "modules_written": modules_written,
        "clips_written":   clips_written,
        "aud_size_bytes":  aud_size,
    }


def verify_against_pharkas(pharkas_dir: Path) -> bool:
    """
    Smoke-test: parse a pharkas AUDIO/N.MAP and verify the referenced offsets
    in AUDIO/RESOURCE.AUD contain valid SOL headers.
    Returns True if at least one entry validates OK.
    """
    audio_dir = pharkas_dir / "AUDIO"
    aud_path  = audio_dir / "RESOURCE.AUD"

    # Pick a small MAP file to test
    maps = sorted(
        (p for p in audio_dir.iterdir() if p.suffix.upper() == ".MAP"),
        key=lambda p: p.stat().st_size,
    )
    if not maps:
        print("No .MAP files found in pharkas AUDIO/")
        return False

    test_map = maps[0]
    print(f"Testing with {test_map.name} ({test_map.stat().st_size} bytes)")

    with open(test_map, "rb") as f:
        data = f.read()

    magic = data[:2]
    base_offset = struct.unpack_from("<I", data, 2)[0]
    print(f"  magic = {magic.hex()}  baseOffset = {hex(base_offset)}")

    ptr = 6
    cumul = 0
    ok_count = 0
    with open(aud_path, "rb") as aud:
        while ptr < len(data):
            noun = data[ptr]
            if noun == 0xFF:
                break
            if ptr + 7 > len(data):
                break
            verb = data[ptr + 1]
            cond = data[ptr + 2]
            seq  = data[ptr + 3] & 0x3F
            co   = struct.unpack_from("<I", data[ptr + 4:ptr + 7] + b"\x00")[0]
            cumul += co
            actual = base_offset + cumul
            ptr += 7

            aud.seek(actual)
            hdr = aud.read(2)
            valid = len(hdr) == 2 and hdr[0] == 0x8D
            status = "OK " if valid else "BAD"
            print(f"  [{status}] n={noun} v={verb} c={cond} s={seq} "
                  f"offset={actual}  hdr={hdr.hex()}")
            if valid:
                ok_count += 1

    return ok_count > 0


if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        ok = verify_against_pharkas(Path("D:/projects/sq5/pharkas"))
        print("Verification:", "PASS" if ok else "FAIL")
    else:
        print("Usage: repackage.py --verify")
        print("       (or import and call repackage() from your tool)")

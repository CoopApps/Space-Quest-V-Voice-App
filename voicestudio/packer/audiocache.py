"""
Audiocache management for SCI audio36 patch files.

Mirrors the SCI Companion audiocache layout:
  audiocache/
    <module>/
      @<3-char module><2-char noun><2-char verb>.<2-char cond><1-char seq>
    uptodate.bin   (tracks which modules need repackaging)

Base36 encoding: digits 0-9 then a-z.
  @[mod:3][noun:2][verb:2].[cond:2][seq:1]
"""

import json
import shutil
from pathlib import Path


_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _to_base36(n: int, width: int) -> str:
    if n < 0:
        raise ValueError(f"Negative value not allowed: {n}")
    digits = []
    while n or len(digits) < width:
        digits.append(_CHARS[n % 36])
        n //= 36
    if len(digits) > width:
        raise ValueError(f"Value {n} overflows base36 field of width {width}")
    return "".join(reversed(digits))


def _from_base36(s: str) -> int:
    return int(s, 36)


def base36_filename(module: int, noun: int, verb: int, cond: int, seq: int) -> str:
    """
    Return the audio36 patch filename for the given tuple.
    Format: @[mod:3][noun:2][verb:2].[cond:2][seq:1]
    """
    return (
        "@"
        + _to_base36(module, 3)
        + _to_base36(noun, 2)
        + _to_base36(verb, 2)
        + "."
        + _to_base36(cond, 2)
        + _to_base36(seq, 1)
    )


def parse_base36_filename(name: str) -> tuple[int, int, int, int, int]:
    """
    Parse a base36 audio36 filename back to (module, noun, verb, cond, seq).
    Accepts names with or without the '@' prefix and with or without extension.
    """
    stem = name.lstrip("@").split(".")[0]
    ext  = name.lstrip("@").split(".")[-1] if "." in name.lstrip("@") else ""

    # stem = mod(3) + noun(2) + verb(2) = 7 chars
    # ext  = cond(2) + seq(1)           = 3 chars
    if len(stem) != 7:
        raise ValueError(f"Unexpected stem length {len(stem)} in '{name}'")

    module = _from_base36(stem[0:3])
    noun   = _from_base36(stem[3:5])
    verb   = _from_base36(stem[5:7])
    cond   = _from_base36(ext[0:2]) if len(ext) >= 2 else 0
    seq    = _from_base36(ext[2:3]) if len(ext) >= 3 else 0
    return module, noun, verb, cond, seq


class AudioCache:
    """
    Manages the audiocache directory for a single SQ5 game installation.

    audiocache_dir is typically  <game_dir>/audiocache/
    Each module (room) gets its own subdirectory.
    """

    def __init__(self, audiocache_dir: Path) -> None:
        self.root = Path(audiocache_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._dirty_path = self.root / "uptodate.bin"
        self._dirty: set[int] = self._load_dirty()

    # ------------------------------------------------------------------
    # Dirty tracking (modules that need repackaging into resource.aud)
    # ------------------------------------------------------------------

    def _load_dirty(self) -> set[int]:
        if self._dirty_path.exists():
            try:
                return set(json.loads(self._dirty_path.read_text()))
            except Exception:
                pass
        return set()

    def _save_dirty(self) -> None:
        self._dirty_path.write_text(json.dumps(sorted(self._dirty)))

    def mark_dirty(self, module: int) -> None:
        self._dirty.add(module)
        self._save_dirty()

    def mark_clean(self, module: int) -> None:
        self._dirty.discard(module)
        self._save_dirty()

    def dirty_modules(self) -> list[int]:
        return sorted(self._dirty)

    # ------------------------------------------------------------------
    # File placement
    # ------------------------------------------------------------------

    def patch_path(self, module: int, noun: int, verb: int,
                   cond: int, seq: int) -> Path:
        """Return the expected path for a given tuple (may not exist yet)."""
        filename = base36_filename(module, noun, verb, cond, seq)
        return self.root / str(module) / filename

    def place_recording(self, module: int, noun: int, verb: int,
                        cond: int, seq: int, src_aud: Path) -> Path:
        """
        Copy a .aud patch file into the audiocache at the correct location.
        Marks the module as dirty. Returns the destination path.
        """
        dest = self.patch_path(module, noun, verb, cond, seq)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_aud, dest)
        self.mark_dirty(module)
        return dest

    def remove_recording(self, module: int, noun: int, verb: int,
                         cond: int, seq: int) -> bool:
        """Delete a patch file if it exists. Returns True if deleted."""
        p = self.patch_path(module, noun, verb, cond, seq)
        if p.exists():
            p.unlink()
            self.mark_dirty(module)
            return True
        return False

    def has_recording(self, module: int, noun: int, verb: int,
                      cond: int, seq: int) -> bool:
        return self.patch_path(module, noun, verb, cond, seq).exists()

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    def all_recordings(self) -> list[dict]:
        """
        Return a list of dicts for every patch file in the cache:
          {module, noun, verb, cond, seq, path}
        """
        results = []
        for module_dir in self.root.iterdir():
            if not module_dir.is_dir() or module_dir.name == "uptodate.bin":
                continue
            for f in module_dir.iterdir():
                if not f.name.startswith("@"):
                    continue
                try:
                    m, n, v, c, s = parse_base36_filename(f.name)
                    results.append({
                        "module": m, "noun": n, "verb": v,
                        "cond": c, "seq": s, "path": f,
                    })
                except ValueError:
                    continue
        return results

    def recording_count(self) -> int:
        return len(self.all_recordings())


if __name__ == "__main__":
    # Self-test: encode/decode round-trip for known tuples
    tests = [
        (200, 4, 0, 1, 1),
        (100, 0, 0, 0, 1),
        (1050, 23, 0, 0, 3),
        (0, 1, 2, 3, 4),
    ]
    print("Base36 filename round-trip test:")
    all_ok = True
    for t in tests:
        fn = base36_filename(*t)
        decoded = parse_base36_filename(fn)
        ok = decoded == t
        all_ok = all_ok and ok
        status = "OK" if ok else "FAIL"
        print(f"  {t} -> '{fn}' -> {decoded}  [{status}]")

    # Verify against known pharkas filename conventions
    # pharkas uses module numbers like 2101 - but those are resource IDs, not room numbers
    # Let's verify module 200, noun 4, verb 0, cond 1, seq 1
    fn = base36_filename(200, 4, 0, 1, 1)
    print(f"\nRoom 200 line (noun=4,verb=0,cond=1,seq=1) -> '{fn}'")
    print("All OK" if all_ok else "FAILURES DETECTED")

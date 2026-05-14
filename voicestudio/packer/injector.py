"""
Patch file injector — deploys audio36 recordings to the SQ5 game directory.

For ScummVM: base36-named patch files placed directly in the game directory
are loaded as audio36 resources, overriding anything in RESOURCE.AUD/SFX.

File naming: @[mod:3][noun:2][verb:2].[cond:2][seq:1]
Example: room 200, noun 4, verb 0, cond 1, seq 1 → @05k0400.011
"""

import shutil
import sqlite3
from pathlib import Path

from voicestudio.packer.audiocache import AudioCache, base36_filename
from voicestudio.audio.sol import wav_to_patch


class Injector:
    def __init__(self, game_dir: Path, audiocache: AudioCache, db_path: Path) -> None:
        self.game_dir   = Path(game_dir)
        self.cache      = audiocache
        self.db_path    = Path(db_path)

    # ------------------------------------------------------------------
    # Single-line injection
    # ------------------------------------------------------------------

    def inject_line(self, module: int, noun: int, verb: int,
                    cond: int, seq: int, wav_path: Path) -> Path:
        """
        Convert a WAV recording to a SCI patch file and deploy it.
        1. Convert WAV → .aud patch file in audiocache
        2. Copy patch file into game directory with correct base36 name
        Returns the deployed path.
        """
        # Step 1: convert to patch format and place in audiocache
        aud_path = self.cache.patch_path(module, noun, verb, cond, seq)
        aud_path.parent.mkdir(parents=True, exist_ok=True)
        wav_to_patch(wav_path, aud_path)
        self.cache.mark_dirty(module)

        # Step 2: deploy to game directory
        dest = self._game_patch_path(module, noun, verb, cond, seq)
        shutil.copy2(aud_path, dest)

        # Step 3: update database
        self._update_db(module, noun, verb, cond, seq,
                        str(wav_path), str(aud_path))
        return dest

    def remove_line(self, module: int, noun: int, verb: int,
                    cond: int, seq: int) -> None:
        """Remove a patch file from both audiocache and game directory."""
        self.cache.remove_recording(module, noun, verb, cond, seq)
        game_path = self._game_patch_path(module, noun, verb, cond, seq)
        if game_path.exists():
            game_path.unlink()
        self._clear_db(module, noun, verb, cond, seq)

    # ------------------------------------------------------------------
    # Bulk deployment
    # ------------------------------------------------------------------

    def rebuild_from_wavs(self) -> tuple[int, int]:
        """
        Re-run wav_to_patch for every recorded line in the DB, overwriting
        the audiocache patch.  Picks up any audio-pipeline improvements
        (e.g. edge fades for click reduction) without the user having to
        Remove + Re-Accept every recording manually.

        Returns (rebuilt, missing) — `missing` counts DB rows whose WAV
        file no longer exists on disk.
        """
        rebuilt = missing = 0
        try:
            con = sqlite3.connect(self.db_path)
            rows = con.execute(
                "SELECT module,noun,verb,cond,seq,wav_path FROM lines "
                "WHERE recorded=1 AND wav_path IS NOT NULL"
            ).fetchall()
            con.close()
        except Exception:
            return 0, 0

        for module, noun, verb, cond, seq, wav_path in rows:
            wav = Path(wav_path) if wav_path else None
            if not wav or not wav.exists():
                missing += 1
                continue
            aud_path = self.cache.patch_path(module, noun, verb, cond, seq)
            aud_path.parent.mkdir(parents=True, exist_ok=True)
            wav_to_patch(wav, aud_path)
            self.cache.mark_dirty(module)
            rebuilt += 1
        return rebuilt, missing

    def deploy_all(self, rebuild: bool = True) -> tuple[int, int, int]:
        """
        Deploy every recorded line from the audiocache to the game dir.

        When `rebuild=True` (default) we re-encode every patch from its
        source WAV first.  This guarantees clips reflect the current
        audio pipeline (fades, resampling, etc.) — important because
        patches in the audiocache may have been created by an older
        version of wav_to_patch.

        Returns (deployed, missing, skipped).
        """
        rebuilt = missing = 0
        if rebuild:
            rebuilt, missing = self.rebuild_from_wavs()

        deployed = skipped = 0
        for rec in self.cache.all_recordings():
            src = rec["path"]
            dest = self._game_patch_path(
                rec["module"], rec["noun"], rec["verb"],
                rec["cond"], rec["seq"]
            )
            shutil.copy2(src, dest)
            deployed += 1
        return deployed, missing, skipped

    def undeploy_all(self) -> int:
        """Remove all audio36 patch files from the game directory."""
        removed = 0
        for f in self.game_dir.iterdir():
            if f.name.startswith("@") and len(f.name) >= 10:
                f.unlink()
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def deployed_patches(self) -> list[Path]:
        return [f for f in self.game_dir.iterdir()
                if f.name.startswith("@") and len(f.name) >= 10]

    def deployment_count(self) -> int:
        return len(self.deployed_patches())

    def is_deployed(self, module: int, noun: int, verb: int,
                    cond: int, seq: int) -> bool:
        return self._game_patch_path(module, noun, verb, cond, seq).exists()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _game_patch_path(self, module: int, noun: int, verb: int,
                         cond: int, seq: int) -> Path:
        return self.game_dir / base36_filename(module, noun, verb, cond, seq)

    def _update_db(self, module: int, noun: int, verb: int, cond: int, seq: int,
                   wav_path: str, sol_path: str) -> None:
        try:
            con = sqlite3.connect(self.db_path)
            con.execute(
                """UPDATE lines SET recorded=1, wav_path=?, sol_path=?
                   WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?""",
                (wav_path, sol_path, module, noun, verb, cond, seq),
            )
            con.commit()
            con.close()
        except Exception:
            pass

    def _clear_db(self, module: int, noun: int, verb: int,
                  cond: int, seq: int) -> None:
        try:
            con = sqlite3.connect(self.db_path)
            con.execute(
                """UPDATE lines SET recorded=0, wav_path=NULL, sol_path=NULL
                   WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?""",
                (module, noun, verb, cond, seq),
            )
            con.commit()
            con.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: injector.py <game_dir> <audiocache_dir>")
        print("  Deploys all recorded patch files to the game directory.")
        sys.exit(1)

    game_dir   = Path(sys.argv[1])
    cache_dir  = Path(sys.argv[2])
    db_path    = cache_dir.parent / "sq5_lines.db"

    cache    = AudioCache(cache_dir)
    injector = Injector(game_dir, cache, db_path)
    deployed, skipped = injector.deploy_all()
    print(f"Deployed {deployed} patch files to {game_dir}")
    print(f"Total patches in game dir: {injector.deployment_count()}")

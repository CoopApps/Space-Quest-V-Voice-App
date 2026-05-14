"""
SQ5 Voice Studio — community web edition.

Hosting target: Railway (Docker).  All paths are resolved from env vars so
the production deployment can mount a persistent volume for the DB and
contributions folder without code changes.

API surface:
  GET  /api/characters              list with counts
  GET  /api/rooms                   list with counts
  GET  /api/lines                   query: character / room / search
  GET  /api/lines/{line_key}        line detail + contributions
  POST /api/lines/{line_key}/upload audio file
  GET  /api/contributions/{id}/audio playable WAV
  POST /api/admin/select/{id}       (admin) make this contribution canonical
  POST /api/admin/clear/{line_key}  (admin) unselect any pick for this line
  DELETE /api/admin/contributions/{id} (admin) delete a contribution
  GET  /api/admin/compile           (admin) build & stream a zip with
                                    {RESOURCE.AUD, resource.map patches,
                                     individual patch files}

line_key = "module-noun-verb-cond-seq"  (5 hyphen-separated ints)
"""

import io
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    FastAPI, HTTPException, UploadFile, File, Form, Header,
    Query, Path as PathParam, Depends,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Re-use voicestudio's existing audio conversion stack untouched.
import sys
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from voicestudio.audio.sol import wav_to_patch                    # noqa: E402
from voicestudio.audio.loader import (                            # noqa: E402
    load_to_wav, AudioLoadError, SUPPORTED_SUFFIXES,
)
from voicestudio.packer.audiocache import AudioCache, base36_filename  # noqa: E402
from voicestudio.packer.repackage import repackage                # noqa: E402

from web.db import init_db, open_db                               # noqa: E402

# ---------------------------------------------------------------------------
# Config (env-overridable)
# ---------------------------------------------------------------------------

DB_PATH        = Path(os.environ.get(
    "SQ5_DB_PATH", "D:/projects/SQ5/voicestudio/sq5_lines.db"))
DATA_DIR       = Path(os.environ.get(
    "SQ5_DATA_DIR", str(Path(__file__).parent / "data")))
ADMIN_TOKEN    = os.environ.get("SQ5_ADMIN_TOKEN", "change-me-in-railway-env")
MAX_UPLOAD_MB  = int(os.environ.get("SQ5_MAX_UPLOAD_MB", "20"))

CONTRIB_DIR    = DATA_DIR / "contributions"
CONTRIB_DIR.mkdir(parents=True, exist_ok=True)

init_db(DB_PATH)

app = FastAPI(title="SQ5 Voice Studio (web)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_line_key(key: str) -> tuple[int, int, int, int, int]:
    parts = key.split("-")
    if len(parts) != 5:
        raise HTTPException(400, "line_key must be module-noun-verb-cond-seq")
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError as e:
        raise HTTPException(400, "line_key parts must be integers") from e


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Admin token required")


def line_dir(module: int, noun: int, verb: int, cond: int, seq: int) -> Path:
    d = CONTRIB_DIR / f"{module}" / f"n{noun}_v{verb}_c{cond}_s{seq}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------

@app.get("/api/characters")
def list_characters():
    with open_db(DB_PATH) as con:
        rows = con.execute("""
            SELECT
              l.talker_id,
              COALESCE(l.character_name, 'Unknown-' || l.talker_id) AS name,
              COUNT(*) AS total,
              COUNT(DISTINCT CASE WHEN c.id IS NOT NULL
                                  THEN l.module || '-' || l.noun || '-' ||
                                       l.verb || '-' || l.cond || '-' || l.seq
                             END) AS with_contributions,
              COUNT(DISTINCT CASE WHEN c.selected=1
                                  THEN l.module || '-' || l.noun || '-' ||
                                       l.verb || '-' || l.cond || '-' || l.seq
                             END) AS selected_count
            FROM lines l
            LEFT JOIN contributions c USING (module, noun, verb, cond, seq)
            WHERE l.talker_id NOT IN (97, 98)
            GROUP BY l.talker_id
            ORDER BY total DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/rooms")
def list_rooms():
    with open_db(DB_PATH) as con:
        rows = con.execute("""
            SELECT
              l.module,
              COUNT(*) AS total,
              COUNT(DISTINCT CASE WHEN c.id IS NOT NULL
                                  THEN l.noun || '-' || l.verb || '-' ||
                                       l.cond || '-' || l.seq
                             END) AS with_contributions,
              COUNT(DISTINCT CASE WHEN c.selected=1
                                  THEN l.noun || '-' || l.verb || '-' ||
                                       l.cond || '-' || l.seq
                             END) AS selected_count
            FROM lines l
            LEFT JOIN contributions c USING (module, noun, verb, cond, seq)
            WHERE l.talker_id NOT IN (97, 98)
            GROUP BY l.module
            ORDER BY l.module
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/lines")
def list_lines(
    character:    int | None = Query(None),
    room:         int | None = Query(None),
    search:       str | None = Query(None),
    needs_review: bool = Query(False),
    limit:        int  = Query(500, le=2000),
):
    where = ["l.talker_id NOT IN (97, 98)"]
    params: list = []
    if character is not None:
        where.append("l.talker_id = ?")
        params.append(character)
    if room is not None:
        where.append("l.module = ?")
        params.append(room)
    if search:
        where.append("l.text LIKE ? COLLATE NOCASE")
        params.append(f"%{search}%")
    if needs_review:
        # Multi-contribution lines that don't yet have a canonical pick.
        where.append("""(
            SELECT COUNT(*) FROM contributions c2
            WHERE c2.module=l.module AND c2.noun=l.noun AND c2.verb=l.verb
                  AND c2.cond=l.cond AND c2.seq=l.seq
        ) >= 2 AND NOT EXISTS (
            SELECT 1 FROM contributions c3
            WHERE c3.module=l.module AND c3.noun=l.noun AND c3.verb=l.verb
                  AND c3.cond=l.cond AND c3.seq=l.seq AND c3.selected=1
        )""")
    where_sql = " AND ".join(where)

    with open_db(DB_PATH) as con:
        rows = con.execute(f"""
            SELECT
              l.module, l.noun, l.verb, l.cond, l.seq,
              l.text,
              COALESCE(l.character_name, 'Unknown-' || l.talker_id) AS character,
              (SELECT COUNT(*) FROM contributions c
                 WHERE c.module=l.module AND c.noun=l.noun AND c.verb=l.verb
                       AND c.cond=l.cond AND c.seq=l.seq) AS contribution_count,
              (SELECT id FROM contributions c
                 WHERE c.module=l.module AND c.noun=l.noun AND c.verb=l.verb
                       AND c.cond=l.cond AND c.seq=l.seq AND c.selected=1
                 LIMIT 1) AS selected_id
            FROM lines l
            WHERE {where_sql}
            ORDER BY l.module, l.noun, l.verb, l.cond, l.seq
            LIMIT ?
        """, params + [limit]).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/lines/{key}")
def line_detail(key: str):
    module, noun, verb, cond, seq = parse_line_key(key)
    with open_db(DB_PATH) as con:
        line = con.execute("""
            SELECT
              l.module, l.noun, l.verb, l.cond, l.seq, l.text,
              COALESCE(l.character_name, 'Unknown-' || l.talker_id) AS character,
              l.talker_id
            FROM lines l
            WHERE l.module=? AND l.noun=? AND l.verb=? AND l.cond=? AND l.seq=?
        """, (module, noun, verb, cond, seq)).fetchone()
        if not line:
            raise HTTPException(404, "Line not found")

        contribs = con.execute("""
            SELECT id, contributor, filename, duration_sec, uploaded_at,
                   selected, note
            FROM contributions
            WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?
            ORDER BY selected DESC, uploaded_at DESC
        """, (module, noun, verb, cond, seq)).fetchall()
    return {
        "line":          dict(line),
        "contributions": [dict(c) for c in contribs],
    }


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.get("/api/contributors/check")
def check_contributor(name: str, token: str = ""):
    """
    Tell the client whether a contributor name is available for them
    to use.  Returns one of:
        {"status": "free"}     — name not yet claimed
        {"status": "yours"}    — claimed and the token matches
        {"status": "taken"}    — claimed by someone else (different token)
    """
    name = (name or "").strip()
    if not name:
        return {"status": "free"}
    with open_db(DB_PATH) as con:
        row = con.execute(
            "SELECT token FROM contributors WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()
    if not row:
        return {"status": "free"}
    if row["token"] == (token or ""):
        return {"status": "yours"}
    return {"status": "taken"}


@app.post("/api/lines/{key}/upload")
async def upload_contribution(
    key: str,
    audio: UploadFile = File(...),
    contributor:       str = Form(...),
    contributor_token: str = Form(...),
    note: str | None   = Form(None),
):
    module, noun, verb, cond, seq = parse_line_key(key)

    # Name is required; trim whitespace and reject empties.
    contributor = (contributor or "").strip()
    contributor_token = (contributor_token or "").strip()
    if not contributor:
        raise HTTPException(400, "A contributor name is required.")
    if contributor.lower() == "anonymous":
        raise HTTPException(400, "Pick a real name; 'anonymous' is not allowed.")
    if not contributor_token:
        raise HTTPException(400, "Missing contributor token. Reload the page.")

    # Validate line exists, and claim / verify the contributor name.
    with open_db(DB_PATH) as con:
        exists = con.execute(
            "SELECT 1 FROM lines WHERE module=? AND noun=? AND verb=? "
            "AND cond=? AND seq=?",
            (module, noun, verb, cond, seq),
        ).fetchone()
        if not exists:
            raise HTTPException(404, "Line not found")

        # If the name is already claimed, the token must match.  Otherwise
        # register this token as the owner of the name.
        row = con.execute(
            "SELECT token FROM contributors WHERE name=? COLLATE NOCASE",
            (contributor,),
        ).fetchone()
        if row:
            if row["token"] != contributor_token:
                raise HTTPException(
                    409,
                    f"The name '{contributor}' is already in use by someone "
                    "else.  Pick a different name.",
                )
        else:
            con.execute(
                "INSERT INTO contributors (name, token) VALUES (?, ?)",
                (contributor, contributor_token),
            )

    # Read upload, size-check.
    raw = await audio.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")

    # Save the raw upload, then decode to canonical WAV via loader.
    orig_suffix = Path(audio.filename or "").suffix.lower() or ".bin"
    if orig_suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported format {orig_suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )

    tmp_in = Path(tempfile.mkstemp(prefix="upload_", suffix=orig_suffix)[1])
    try:
        tmp_in.write_bytes(raw)
        try:
            decoded_path, is_temp = load_to_wav(tmp_in)
        except AudioLoadError as e:
            raise HTTPException(400, str(e))

        # Store final WAV under contributions/<module>/<line>/<uuid>.wav
        d   = line_dir(module, noun, verb, cond, seq)
        uid = uuid.uuid4().hex[:12]
        final = d / f"{uid}.wav"
        shutil.copyfile(decoded_path, final)
        if is_temp:
            try: decoded_path.unlink()
            except OSError: pass

        # Compute duration via wave.
        import wave
        try:
            with wave.open(str(final), "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
        except Exception:
            dur = None

        with open_db(DB_PATH) as con:
            cur = con.execute(
                """INSERT INTO contributions
                   (module, noun, verb, cond, seq, contributor,
                    filename, duration_sec, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (module, noun, verb, cond, seq,
                 contributor.strip() or "anonymous",
                 final.name, dur, note),
            )
            new_id = cur.lastrowid
    finally:
        try: tmp_in.unlink()
        except OSError: pass

    return {"id": new_id, "filename": final.name, "duration_sec": dur}


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

@app.get("/api/contributions/{contribution_id}/audio")
def get_contribution_audio(contribution_id: int):
    with open_db(DB_PATH) as con:
        row = con.execute(
            "SELECT module,noun,verb,cond,seq,filename FROM contributions "
            "WHERE id=?",
            (contribution_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Contribution not found")
    path = line_dir(row["module"], row["noun"], row["verb"],
                    row["cond"], row["seq"]) / row["filename"]
    if not path.exists():
        raise HTTPException(404, "Audio file missing on disk")
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    """Counts the admin cares about: how much work remains."""
    with open_db(DB_PATH) as con:
        row = con.execute("""
            SELECT
              (SELECT COUNT(*) FROM lines WHERE talker_id NOT IN (97,98))
                AS total_lines,
              (SELECT COUNT(DISTINCT module || '-' || noun || '-' || verb || '-' || cond || '-' || seq)
                 FROM contributions) AS lines_with_contribs,
              (SELECT COUNT(*)
                 FROM contributions WHERE selected=1) AS canonical_picks,
              (SELECT COUNT(*) FROM contributions)    AS total_contribs
        """).fetchone()
        # "Needs review" = lines with ≥2 contributions but no canonical.
        needs = con.execute("""
            SELECT COUNT(*) FROM (
                SELECT module, noun, verb, cond, seq
                FROM contributions
                GROUP BY module, noun, verb, cond, seq
                HAVING COUNT(*) >= 2
                   AND SUM(selected) = 0
            )
        """).fetchone()[0]
    return {
        "total_lines":         row["total_lines"],
        "lines_with_contribs": row["lines_with_contribs"],
        "canonical_picks":     row["canonical_picks"],
        "total_contribs":      row["total_contribs"],
        "needs_review":        needs,
    }


@app.post("/api/admin/select/{contribution_id}", dependencies=[Depends(require_admin)])
def admin_select(contribution_id: int):
    """
    Mark this contribution canonical, and cascade: every other
    contribution by the same contributor for the same character (talker)
    also becomes canonical on its line (replacing any prior pick).

    Rationale — once we've decided "this voice IS the character", every
    line that contributor recorded for that character should win by
    default.  The admin can still override individually after the cascade.
    """
    with open_db(DB_PATH) as con:
        row = con.execute(
            "SELECT module, noun, verb, cond, seq, contributor "
            "FROM contributions WHERE id=?",
            (contribution_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Contribution not found")

        # Resolve the line → talker_id so we know which character we're
        # cascading across.
        line = con.execute(
            "SELECT talker_id FROM lines WHERE module=? AND noun=? "
            "AND verb=? AND cond=? AND seq=?",
            (row["module"], row["noun"], row["verb"], row["cond"], row["seq"]),
        ).fetchone()
        talker_id = line["talker_id"] if line else None

        # Step 1 — clear current selection on THIS line, then mark our pick.
        con.execute(
            "UPDATE contributions SET selected=0 "
            "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
            (row["module"], row["noun"], row["verb"], row["cond"], row["seq"]),
        )
        con.execute(
            "UPDATE contributions SET selected=1 WHERE id=?",
            (contribution_id,),
        )
        cascaded_lines = 1

        # Step 2 — cascade across other lines for the same talker.
        if talker_id is not None and row["contributor"]:
            # Every other contribution by this person on a line belonging
            # to the same character.
            siblings = con.execute("""
                SELECT c.id, c.module, c.noun, c.verb, c.cond, c.seq
                FROM contributions c
                JOIN lines l USING (module, noun, verb, cond, seq)
                WHERE c.contributor=? COLLATE NOCASE
                  AND l.talker_id=?
                  AND c.id != ?
            """, (row["contributor"], talker_id, contribution_id)).fetchall()

            promoted_line_keys = set()
            for s in siblings:
                lk = (s["module"], s["noun"], s["verb"], s["cond"], s["seq"])
                # Only one canonical per line — if we've already promoted
                # one sibling for this line in this pass, skip subsequent
                # ones (the user has multiple takes on the same line).
                if lk in promoted_line_keys:
                    continue
                con.execute(
                    "UPDATE contributions SET selected=0 "
                    "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
                    lk,
                )
                con.execute(
                    "UPDATE contributions SET selected=1 WHERE id=?",
                    (s["id"],),
                )
                promoted_line_keys.add(lk)
                cascaded_lines += 1

    return {"ok": True, "cascaded_lines": cascaded_lines}


@app.post("/api/admin/clear/{key}", dependencies=[Depends(require_admin)])
def admin_clear(key: str):
    module, noun, verb, cond, seq = parse_line_key(key)
    with open_db(DB_PATH) as con:
        con.execute(
            "UPDATE contributions SET selected=0 "
            "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
            (module, noun, verb, cond, seq),
        )
    return {"ok": True}


@app.delete(
    "/api/admin/contributions/{contribution_id}",
    dependencies=[Depends(require_admin)],
)
def admin_delete(contribution_id: int):
    with open_db(DB_PATH) as con:
        row = con.execute(
            "SELECT module,noun,verb,cond,seq,filename FROM contributions "
            "WHERE id=?",
            (contribution_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Contribution not found")
        path = line_dir(row["module"], row["noun"], row["verb"],
                        row["cond"], row["seq"]) / row["filename"]
        if path.exists():
            try: path.unlink()
            except OSError: pass
        con.execute("DELETE FROM contributions WHERE id=?", (contribution_id,))
    return {"ok": True}


@app.get("/api/admin/compile/aud", dependencies=[Depends(require_admin)])
def admin_compile_full_aud():
    """
    Build a complete AUDIO/ folder set (RESOURCE.AUD + per-module MAPs +
    master 65535.MAP) from every `selected` contribution and stream it
    back as a zip.  This matches what the desktop tool's "Build
    RESOURCE.AUD" button produces — drop the AUDIO/ folder into the SQ5
    install (or distribute it) and the engine picks up every recording
    in one bundle, no per-line patch files needed.
    """
    with open_db(DB_PATH) as con:
        rows = con.execute("""
            SELECT module, noun, verb, cond, seq, filename
            FROM contributions
            WHERE selected=1
            ORDER BY module, noun, verb, cond, seq
        """).fetchall()
    if not rows:
        raise HTTPException(400, "Nothing is selected yet — pick recordings first.")

    # Build a transient audiocache full of SOL patches for every
    # selected contribution, then call the shared `repackage()` pipeline
    # against it.  We work entirely in a tempdir so concurrent admin
    # compiles never clobber each other.
    with tempfile.TemporaryDirectory(prefix="sq5_compile_") as workdir:
        workroot = Path(workdir)
        cache_dir   = workroot / "audiocache"
        build_dir   = workroot / "build"
        cache_dir.mkdir(); build_dir.mkdir()

        for r in rows:
            wav_path = line_dir(r["module"], r["noun"], r["verb"],
                                r["cond"], r["seq"]) / r["filename"]
            if not wav_path.exists():
                continue
            out = cache_dir / str(r["module"]) / base36_filename(
                r["module"], r["noun"], r["verb"], r["cond"], r["seq"],
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            wav_to_patch(wav_path, out)

        cache    = AudioCache(cache_dir)
        summary  = repackage(build_dir, cache)
        audio_dir = build_dir / "AUDIO"
        if not audio_dir.exists():
            raise HTTPException(500, "Build produced no AUDIO folder")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in audio_dir.iterdir():
                zf.write(f, arcname=f"AUDIO/{f.name}")

    buf.seek(0)
    fname = (
        f"sq5_audio_{summary['clips_written']}clips_"
        f"{summary['modules_written']}modules.zip"
    )
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/admin/compile", dependencies=[Depends(require_admin)])
def admin_compile():
    """
    Build individual SCI audio36 patch files from every `selected`
    contribution and stream them as a zip.  Drop them straight into the
    SQ5 game folder to override audio one line at a time.

    Use /api/admin/compile/aud for the bundled RESOURCE.AUD + MAPs set
    (matches desktop's "Build RESOURCE.AUD" output).
    """
    with open_db(DB_PATH) as con:
        rows = con.execute("""
            SELECT module, noun, verb, cond, seq, filename
            FROM contributions
            WHERE selected=1
            ORDER BY module, noun, verb, cond, seq
        """).fetchall()

    if not rows:
        raise HTTPException(400, "Nothing is selected yet — pick recordings first.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for r in rows:
            wav_path = line_dir(r["module"], r["noun"], r["verb"],
                                r["cond"], r["seq"]) / r["filename"]
            if not wav_path.exists():
                continue
            patch_name = base36_filename(
                r["module"], r["noun"], r["verb"], r["cond"], r["seq"],
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".aud") as tmp:
                tmp_path = Path(tmp.name)
            try:
                wav_to_patch(wav_path, tmp_path)
                zf.write(tmp_path, arcname=patch_name)
            finally:
                try: tmp_path.unlink()
                except OSError: pass

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                'attachment; filename="sq5_voice_patches.zip"',
        },
    )


# ---------------------------------------------------------------------------
# Static frontend (served last so /api/* takes precedence)
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

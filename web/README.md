# SQ5 Voice Studio — community web edition

A FastAPI + vanilla-JS web app for collecting community voice recordings for
the SQ5 voice-acting mod.  Mirrors most of the desktop tool's UX (browse by
character or room, search by text, listen, contribute) but with a public
multi-user model.

Visitors can:
- Browse every line in the game and search by text
- Listen to recordings already submitted by others
- Drop an audio file (WAV / MP3 / FLAC / OGG / M4A / AAC / AIFF) onto a line
- Or record their take directly in the browser (mic permission required)

The site owner (you) can:
- Pick which contribution becomes the canonical recording for each line
- Delete unwanted submissions
- Download a zip of SCI audio36 patch files for the selected set — drop those
  straight into the SQ5 game folder, no `resource.aud` rebuild needed

Admin actions require the `X-Admin-Token` header.  Set this token via the
input at the bottom-right of the page; it's stored in localStorage for the
session.  The server validates it against the `SQ5_ADMIN_TOKEN` env var.

## Local development

```bash
cd D:/projects/SQ5
pip install -r web/requirements.txt
SQ5_ADMIN_TOKEN=dev uvicorn web.app:app --reload --port 8000
# open http://localhost:8000
```

The first run creates the `contributions` table inside `sq5_lines.db` if it
isn't there already.  Uploaded audio is written under `web/data/contributions/`
by default (override via `SQ5_DATA_DIR`).

## Railway deployment

```bash
# In the Railway project, set these env vars:
#   SQ5_ADMIN_TOKEN  = (a long random secret)
#   SQ5_MAX_UPLOAD_MB = 20  (optional, default 20)
#
# Mount a persistent volume at /data so contributions and the DB survive
# redeploys.  The Dockerfile sets SQ5_DATA_DIR=/data and
# SQ5_DB_PATH=/data/sq5_lines.db.
#
# Seed the volume with sq5_lines.db on first deploy (e.g. via railway run
# or by copying it through a shell into /data).
```

`railway.json` points Railway at `web/Dockerfile` and sets a healthcheck on
`/api/characters`.

## API summary

| Method | Path | Notes |
|---|---|---|
| GET | `/api/characters` | List with done/total counts |
| GET | `/api/rooms` | Same, grouped by room |
| GET | `/api/lines?character=&room=&search=` | Catalog of lines |
| GET | `/api/lines/{key}` | Line detail + contributions |
| POST | `/api/lines/{key}/upload` | Multipart audio upload |
| GET | `/api/contributions/{id}/audio` | Stream playable WAV |
| POST | `/api/admin/select/{id}` | Mark this contribution canonical |
| POST | `/api/admin/clear/{key}` | Clear any selection on this line |
| DELETE | `/api/admin/contributions/{id}` | Delete |
| GET | `/api/admin/compile` | Zip of audio36 patches |

`key = "{module}-{noun}-{verb}-{cond}-{seq}"`.

## What this does not do

- Rebuild a full top-level `RESOURCE.AUD` from scratch.  The compiled zip
  contains audio36 *patch files* that override entries in the shipped
  `RESOURCE.AUD` at runtime — same mechanism the existing voice mod uses for
  individual replacements.  If you want a single concatenated AUD instead,
  the existing `voicestudio/packer/repackage.py` does that — invoke it
  server-side or run it locally over the downloaded patches.
- Authenticate non-admin users.  Anyone with the URL can submit a recording.
  Add a hCaptcha / rate-limit layer if abuse becomes a problem.
